#!/usr/bin/env python3
"""Quantify the numerical spread between FFTW_MEASURE runs (bench#36 Phase B).

usage: compare_fftw_dumps.py <tag> [<tag> ...]

Reads run<N>.<label>_r2c.bin dumps -- the forward-transform output for a
byte-identical, deterministic input -- and reports, per transform, how many
distinct results the timing-based planner produced and how far apart they are
in absolute and ULP terms.
"""
import os
import sys
from pathlib import Path

import numpy as np

BASE = Path(os.environ.get("STEP2_BASE",
            os.path.expanduser("~/scratch/bench36_step2_20260826"))) / "fftw_probe"


def ulp_distance(a, b):
    """Distance in float32 ULPs between two float32 arrays."""
    ai = a.view(np.int32).astype(np.int64)
    bi = b.view(np.int32).astype(np.int64)
    # map to a monotonic ordering across the sign boundary
    ai = np.where(ai < 0, np.int64(0x80000000) - ai, ai)
    bi = np.where(bi < 0, np.int64(0x80000000) - bi, bi)
    return np.abs(ai - bi)


for tag in (sys.argv[1:] or ["idle"]):
    d = BASE / f"dump_{tag}"
    print(f"\n# tag: {tag}   ({d})")
    if not d.is_dir():
        print("  no dump directory")
        continue
    labels = sorted({p.name.split(".", 1)[1] for p in d.glob("run*.bin")})
    for lab in labels:
        runs = sorted(d.glob(f"run*.{lab}"),
                      key=lambda p: int(p.name[3:p.name.index(".")]))
        arrs = [np.fromfile(p, dtype=np.float32) for p in runs]
        digests = [a.tobytes() for a in arrs]
        uniq = {}
        for i, dg in enumerate(digests, 1):
            uniq.setdefault(dg, []).append(i)
        print(f"  {lab}: {len(runs)} runs, {len(uniq)} distinct results "
              f"(groups: {[v for v in uniq.values()]})")
        if len(uniq) == 1:
            continue
        ref = arrs[0]
        worst_abs = worst_rel = 0.0
        worst_ulp = 0
        for i, a in enumerate(arrs[1:], 2):
            if a.tobytes() == ref.tobytes():
                continue
            diff = np.abs(a.astype(np.float64) - ref.astype(np.float64))
            scale = np.maximum(np.abs(ref.astype(np.float64)), 1e-30)
            u = ulp_distance(ref, a)
            worst_abs = max(worst_abs, diff.max())
            worst_rel = max(worst_rel, (diff / scale).max())
            worst_ulp = max(worst_ulp, int(u.max()))
            n = int(np.count_nonzero(diff))
            print(f"    run1 vs run{i}: n_diff={n}/{diff.size} "
                  f"({n/diff.size:.2e})  max|d|={diff.max():.3e}  "
                  f"max_ulp={int(u.max())}")
        print(f"    worst over all pairs vs run1: max|d|={worst_abs:.3e}  "
              f"max_rel={worst_rel:.3e}  max_ulp={worst_ulp}")
