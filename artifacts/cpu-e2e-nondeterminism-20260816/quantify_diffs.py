#!/usr/bin/env python3
"""Quantify the differing datasets from the CPU E2E A/B bitwise pass.

Hypothesis under test: the diffs originate in dense_offsets (CPU Ampcor
run-to-run nondeterminism) — offsets should differ at a small number of
windows by 1/32-pixel quanta, correlationSurfacePeak by float noise, and
everything else is downstream cascade. The patch target (mask layers)
is already bitwise identical.
"""
import h5py
import numpy as np

CASES = [
    ("RIFG", "/ab/control/scratch/RIFG.h5", "/ab/treat/scratch/RIFG.h5", [
        "science/LSAR/RIFG/swaths/frequencyA/pixelOffsets/HH/alongTrackOffset",
        "science/LSAR/RIFG/swaths/frequencyA/pixelOffsets/HH/slantRangeOffset",
        "science/LSAR/RIFG/swaths/frequencyA/pixelOffsets/HH/correlationSurfacePeak",
        "science/LSAR/RIFG/swaths/frequencyA/interferogram/HH/coherenceMagnitude",
    ]),
    ("RUNW", "/ab/control/scratch/RUNW.h5", "/ab/treat/scratch/RUNW.h5", [
        "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/unwrappedPhase",
        "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/connectedComponents",
    ]),
]

for label, pa, pb, names in CASES:
    with h5py.File(pa, "r") as fa, h5py.File(pb, "r") as fb:
        for n in names:
            da = fa[n][...]
            db = fb[n][...]
            neq = da != db
            both_nan = np.zeros_like(neq)
            if np.issubdtype(da.dtype, np.floating):
                both_nan = np.isnan(da) & np.isnan(db)
            neq = neq & ~both_nan
            cnt = int(neq.sum())
            frac = cnt / da.size
            print(f"{label} {n.split('/')[-1]}: differing={cnt}/{da.size} ({frac:.2e})")
            if cnt and np.issubdtype(da.dtype, np.floating):
                d = (db.astype(np.float64) - da.astype(np.float64))[neq]
                print(f"    max|d|={np.nanmax(np.abs(d)):.6g}  "
                      f"uniq_first10={np.unique(np.round(d, 9))[:10]}")
                if "Offset" in n:
                    q = np.unique(np.round(d * 32, 6))
                    print(f"    deltas*32 uniq_first10={q[:10]}")
