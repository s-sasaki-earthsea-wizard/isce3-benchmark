#!/usr/bin/env python3
"""Recount all 36 pairs of the bench#36 Step 2 standalone dense_offsets runs.

usage: STEP2_BASE=<dir with dof_{idle,load,omp1}_{1,2,3}> recount_pairs.py

Groups the 9 runs by exact sha256 of `snr` + `covariance` (raw-correlation
stage products) and reports within-group vs between-group difference
statistics for `dense_offsets` and `correlation_peak`. Produced for
CORRECTION_2026-09-13.md; reads the replicate directories read-only.
"""
import hashlib
import itertools
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

BASE = Path(os.environ.get("STEP2_BASE",
            os.path.expanduser("~/scratch/bench36_step2_20260826")))
SHAPE_PEAK = (545, 703)        # correlation_peak / snr: 383,135 px
SHAPE_OFF = (545, 703, 2)      # dense_offsets: 766,270 components
RUNS = [(t, r) for t in ("idle", "load", "omp1") for r in (1, 2, 3)]


def raster(tag, rep, name, shape):
    p = BASE / f"dof_{tag}_{rep}" / "scratch" / "dense_offsets" / "freqA" / "HH" / name
    return np.fromfile(p, dtype=np.float32).reshape(shape)


def digest(tag, rep, name):
    p = BASE / f"dof_{tag}_{rep}" / "scratch" / "dense_offsets" / "freqA" / "HH" / name
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def main():
    peak = {k: raster(*k, "correlation_peak", SHAPE_PEAK) for k in RUNS}
    off = {k: raster(*k, "dense_offsets", SHAPE_OFF) for k in RUNS}
    group = {k: (digest(*k, "snr"), digest(*k, "covariance")) for k in RUNS}

    members = defaultdict(list)
    for k, g in group.items():
        members[g].append(f"{k[0]}{k[1]}")
    print("# groups by exact snr+covariance sha256")
    for i, (g, m) in enumerate(sorted(members.items(), key=lambda x: -len(x[1]))):
        print(f"  G{i}: {m}  (snr={g[0]} cov={g[1]})")

    within, between = defaultdict(list), defaultdict(list)
    for x, y in itertools.combinations(RUNS, 2):
        dp = np.abs(peak[x] - peak[y])
        do = np.abs(off[x] - off[y])
        bucket = within if group[x] == group[y] else between
        bucket["off_max"].append(do.max())
        bucket["peak_max"].append(dp.max())
        bucket["peak_frac"].append((dp > 0).mean() * 100)
        big = do > 1.0
        bucket["off_comp_gt1"].append(int(big.sum()))
        bucket["off_px_gt1"].append(int(big.any(axis=2).sum()))
        bucket["peak_gt1e3"].append(int((dp > 1e-3).sum()))

    for label, b in (("within-group", within), ("between-group", between)):
        n = len(b["off_max"])
        print(f"\n# {label} pairs: n={n}")
        print(f"  dense_offsets max            = {max(b['off_max']):.5f} px")
        print(f"  correlation_peak max         = {max(b['peak_max']):.16e}")
        print(f"  correlation_peak differing px= {min(b['peak_frac']):.4f}% .. {max(b['peak_frac']):.4f}%")
        print(f"  |d offset|>1px components    = {min(b['off_comp_gt1'])} .. {max(b['off_comp_gt1'])} / {SHAPE_OFF[0]*SHAPE_OFF[1]*2}")
        print(f"  |d offset|>1px match positions= {min(b['off_px_gt1'])} .. {max(b['off_px_gt1'])} / {SHAPE_PEAK[0]*SHAPE_PEAK[1]}")
        print(f"  |d peak|>1e-3 px             = {min(b['peak_gt1e3'])} .. {max(b['peak_gt1e3'])}")


if __name__ == "__main__":
    main()
