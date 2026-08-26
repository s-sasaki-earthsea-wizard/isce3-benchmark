#!/usr/bin/env python3
"""Pristine-vs-pristine control: today's unpatched CPU E2E control run
vs the 2026-08-10 unpatched CPU run2 (same runconfig, same v0.25.16
build). If the same diff pattern appears (ULP-level pixelOffsets noise,
2*pi unwrappedPhase shifts) with masks identical, the A/B diffs are
CPU-Ampcor run-to-run nondeterminism, not the patch.
"""
import h5py
import numpy as np

A = "/ab/control/out/product.h5"      # today, unpatched
B = "/run2/product.h5"                # 2026-08-10, unpatched

NAMES = [
    "science/LSAR/GUNW/grids/frequencyA/pixelOffsets/HH/alongTrackOffset",
    "science/LSAR/GUNW/grids/frequencyA/pixelOffsets/HH/slantRangeOffset",
    "science/LSAR/GUNW/grids/frequencyA/pixelOffsets/HH/correlationSurfacePeak",
    "science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase",
    "science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/connectedComponents",
    "science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/mask",
    "science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/HH/mask",
]

with h5py.File(A, "r") as fa, h5py.File(B, "r") as fb:
    for n in NAMES:
        if n not in fa or n not in fb:
            print(f"{n.split('/')[-2]}/{n.split('/')[-1]}: MISSING in one file")
            continue
        da = fa[n][...]
        db = fb[n][...]
        neq = da != db
        if np.issubdtype(da.dtype, np.floating):
            neq &= ~(np.isnan(da) & np.isnan(db))
        cnt = int(neq.sum())
        line = f"{n.split('/')[-2]}/{n.split('/')[-1]}: differing={cnt}/{da.size} ({cnt/da.size:.2e})"
        if cnt and np.issubdtype(da.dtype, np.floating):
            d = (db.astype(np.float64) - da.astype(np.float64))[neq]
            line += f"  max|d|={np.nanmax(np.abs(d)):.6g}"
        print(line)
