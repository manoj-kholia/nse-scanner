#!/usr/bin/env python3
"""
trading_costs.py
----------------
What a trade actually costs in India, and therefore how far price has to move
before you have made a single rupee.

This module exists because of one line in SEBI's July 2024 study of intraday
traders in the equity cash segment: loss-makers paid transaction costs worth an
ADDITIONAL 57% of their trading losses, while profit-makers spent 19% of their
profits on costs. Costs were not a rounding error on the outcome - they were
close to the whole difference. So every intraday signal this repo produces
carries its own break-even number, computed here, instead of leaving you to
assume the move is worth taking.

Intraday equity (MIS) and delivery (CNC) are charged very differently: STT is
0.025% on the sell side only for intraday, against 0.1% on BOTH sides for
delivery. Intraday looks cheaper per trade, which is exactly the trap - you
pay it many more times.

RATES CHANGE. These defaults are typical discount-broker rates and are
deliberately easy to override. Check them against your own contract note
before you trust the break-even numbers; if your broker charges more, every
number this produces is optimistic.
"""

from dataclasses import dataclass, replace

# Typical discount-broker rates. Verify against your contract note.
BROKERAGE_PCT = 0.0003        # 0.03% per executed order...
BROKERAGE_CAP = 20.0          # ...capped at Rs 20 per order
STT_INTRADAY_SELL = 0.00025   # 0.025%, sell side only
STT_DELIVERY = 0.001          # 0.1%, both sides
EXCHANGE_TXN = 0.0000297      # NSE equity, both sides
SEBI_FEE = 0.000001           # Rs 10 per crore
STAMP_INTRADAY_BUY = 0.00003  # 0.003%, buy side only
STAMP_DELIVERY_BUY = 0.00015  # 0.015%, buy side only
GST = 0.18                    # on brokerage + exchange + SEBI fee
DEFAULT_SLIPPAGE = 0.0005     # 0.05% per side - an assumption, not a fee


@dataclass(frozen=True)
class Costs:
    """One round trip, in rupees and as a percentage of the position."""
    turnover: float
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    slippage: float

    @property
    def total(self):
        return (self.brokerage + self.stt + self.exchange + self.sebi
                + self.stamp + self.gst + self.slippage)

    @property
    def pct_of_position(self):
        """Round-trip cost as a % of the money you put in."""
        entry = self.turnover / 2
        return (self.total / entry * 100) if entry else 0.0

    @property
    def breakeven_move_pct(self):
        """How far price must move, in %, just to get back to flat."""
        return self.pct_of_position

    def explain(self):
        rows = [("Brokerage", self.brokerage), ("STT", self.stt),
                ("Exchange", self.exchange), ("SEBI", self.sebi),
                ("Stamp duty", self.stamp), ("GST", self.gst),
                ("Slippage (assumed)", self.slippage)]
        w = max(len(r[0]) for r in rows)
        out = [f"  {n:<{w}}  Rs {v:8.2f}" for n, v in rows if v]
        out.append(f"  {'TOTAL':<{w}}  Rs {self.total:8.2f}"
                   f"   = {self.pct_of_position:.3f}% of the position")
        return "\n".join(out)


def round_trip(entry_price, qty, intraday=True, slippage=DEFAULT_SLIPPAGE,
               brokerage_pct=BROKERAGE_PCT, brokerage_cap=BROKERAGE_CAP):
    """Cost of buying `qty` at `entry_price` and selling it back at the same price.

    Costing the round trip at a flat price isolates the FEES from the trade's
    outcome, which is what you want when asking "how far must this move before
    it is worth doing".
    """
    entry_price, qty = float(entry_price), float(qty)
    if entry_price <= 0 or qty <= 0:
        return Costs(0, 0, 0, 0, 0, 0, 0, 0)

    side = entry_price * qty
    turnover = side * 2

    brokerage = 2 * min(side * brokerage_pct, brokerage_cap)
    stt = side * (STT_INTRADAY_SELL if intraday else STT_DELIVERY * 2)
    exchange = turnover * EXCHANGE_TXN
    sebi = turnover * SEBI_FEE
    stamp = side * (STAMP_INTRADAY_BUY if intraday else STAMP_DELIVERY_BUY)
    gst = (brokerage + exchange + sebi) * GST
    slip = turnover * slippage

    return Costs(turnover, brokerage, stt, exchange, sebi, stamp, gst, slip)


def breakeven_pct(entry_price, position_value, intraday=True,
                  slippage=DEFAULT_SLIPPAGE):
    """Break-even move for a position of roughly `position_value` rupees."""
    if entry_price <= 0 or position_value <= 0:
        return None
    qty = max(1, int(position_value / entry_price))
    return round(round_trip(entry_price, qty, intraday, slippage).breakeven_move_pct, 3)


def edge_after_costs(expected_move_pct, entry_price, position_value,
                     intraday=True, slippage=DEFAULT_SLIPPAGE):
    """What is left of an expected move once costs are paid, in percentage points.

    Negative means the setup cannot pay for itself even when it works.
    """
    be = breakeven_pct(entry_price, position_value, intraday, slippage)
    if be is None:
        return None
    return round(float(expected_move_pct) - be, 3)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="What does a trade actually cost?")
    ap.add_argument("price", type=float)
    ap.add_argument("--value", type=float, default=100000, help="position size in rupees")
    ap.add_argument("--delivery", action="store_true")
    ap.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    a = ap.parse_args()

    qty = max(1, int(a.value / a.price))
    c = round_trip(a.price, qty, intraday=not a.delivery, slippage=a.slippage)
    kind = "DELIVERY" if a.delivery else "INTRADAY"
    print(f"{kind}: {qty} shares at Rs {a.price:,.2f}  "
          f"(position Rs {a.price * qty:,.0f}, round trip Rs {c.turnover:,.0f})\n")
    print(c.explain())
    print(f"\n  Price must move {c.breakeven_move_pct:.3f}% just to break even.")
