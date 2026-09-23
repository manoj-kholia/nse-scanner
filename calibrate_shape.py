#!/usr/bin/env python3
"""
calibrate_shape.py
------------------
Pick a cup-shape measure that does not flip on a rupee.

The old test counted how many closes sat in the bottom third of the cup:

    roundness = (closes <= cup_low + range/3).sum() / length

That is a COUNT against a threshold, so it is discontinuous. One bar moving a
rupee across the line moves the score by 1/length - on a 37-bar cup, 0.027.
SPLPETRO scored 0.32 in the scanner and 0.27 on the chart against a 0.30
cutoff, purely because two closes near the line landed on opposite sides in
two different data feeds. Same cup, same rims, same low to the paisa; opposite
verdicts.

The replacement has to be continuous: move one close by a rupee and the score
moves by a rupee's worth, not by a whole bar.

    depth_score = mean( (rim_high - close_i) / (rim_high - cup_low) )

A U spends most of its bars near the bottom, so the mean normalised depth is
high. A V passes through the bottom once, so it is lower. Nothing is counted,
so nothing can tip over.

This script measures both on reference shapes and reports where a cutoff has
to sit to reproduce the old decisions. Run it before changing the threshold.
"""

import numpy as np


def shapes(n=60):
    """Reference cups, drawn the way O'Neil describes them."""
    t = np.linspace(0, 1, n)
    out = {}

    # A sharp spike bottom: flat near the rim, one brief plunge. O'Neil's
    # classic "V-bottom that fails" - no time spent building a base.
    v = np.ones(n)
    mid = n // 2
    spike = 3
    for i in range(n):
        d = abs(i - mid)
        v[i] = 0.0 if d == 0 else min(1.0, d / spike)
    out["sharp spike V"] = v

    # A wide straight-line V: down at a constant rate, up at a constant rate.
    out["wide straight V"] = np.abs(2 * t - 1)

    # A rounded U: a cosine bowl, the textbook shape.
    out["rounded U"] = (1 - np.cos(2 * np.pi * t)) / 2

    # A flat-bottomed saucer - rounder still.
    s = np.clip(np.abs(2 * t - 1) * 2.2 - 1.2, 0, 1)
    out["flat saucer"] = s

    # A late-dipping cup, low near the right rim. Should score like a V.
    out["late V"] = np.abs((t ** 2.6) * 2 - 1)
    return out


def old_roundness(h):
    """h is normalised height in [0, 1]; 1 = rim, 0 = cup low."""
    return float((h <= 1 / 3).sum()) / len(h)


def depth_score(h):
    return float(np.mean(1.0 - h))


def jitter_sensitivity(h, metric, rupees=0.004, trials=400, seed=0):
    """How much does the score move when the closes wobble by a few paisa?

    This is the number that matters. The old measure's answer is what broke
    the two implementations apart.
    """
    rng = np.random.default_rng(seed)
    base = metric(h)
    moved = [abs(metric(np.clip(h + rng.normal(0, rupees, len(h)), 0, 1)) - base)
             for _ in range(trials)]
    return base, float(np.mean(moved)), float(np.max(moved))


def main():
    print(f"{'shape':<18} {'old count':>10} {'jitter':>8} {'max':>7}   "
          f"{'depth score':>12} {'jitter':>8} {'max':>7}")
    print("-" * 78)
    rows = {}
    for name, h in shapes().items():
        o_base, o_avg, o_max = jitter_sensitivity(h, old_roundness)
        d_base, d_avg, d_max = jitter_sensitivity(h, depth_score)
        rows[name] = (o_base, d_base)
        print(f"{name:<18} {o_base:>10.3f} {o_avg:>8.4f} {o_max:>7.4f}   "
              f"{d_base:>12.3f} {d_avg:>8.4f} {d_max:>7.4f}")

    print()
    # The old cutoff was 0.30: it rejected the sharp spike and accepted the
    # wide V and the U. Find the depth-score cutoff that makes the same calls.
    rejected = [d for n, (o, d) in rows.items() if o < 0.30]
    accepted = [d for n, (o, d) in rows.items() if o >= 0.30]
    print(f"old cutoff 0.30 rejects: {[n for n,(o,d) in rows.items() if o < 0.30]}")
    print(f"old cutoff 0.30 accepts: {[n for n,(o,d) in rows.items() if o >= 0.30]}")
    if rejected and accepted:
        lo, hi = max(rejected), min(accepted)
        print(f"\ndepth score: worst accepted {hi:.3f}, best rejected {lo:.3f}")
        if hi > lo:
            print(f"  a cutoff anywhere in ({lo:.3f}, {hi:.3f}) reproduces the old calls")
            print(f"  midpoint -> {(lo + hi) / 2:.2f}")
        else:
            print("  NO cutoff reproduces the old calls - the two measures disagree "
                  "about these shapes, which has to be reported, not hidden.")


if __name__ == "__main__":
    main()
