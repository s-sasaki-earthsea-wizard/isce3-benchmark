#!/usr/bin/env python3
"""Bitwise dataset-by-dataset comparison of the CPU E2E A/B products.

Same discipline as the 2026-08-13 GPU E2E comparison: every HDF5
dataset in the GUNW product and the RIFG/RUNW scratch skeletons is
compared byte-for-byte between the control (pristine utils.py) and
treatment (vectorized utils.py) runs.
"""
import h5py

PAIRS = [
    ("GUNW", "/ab/control/out/product.h5", "/ab/treat/out/product.h5"),
    ("RIFG", "/ab/control/scratch/RIFG.h5", "/ab/treat/scratch/RIFG.h5"),
    ("RUNW", "/ab/control/scratch/RUNW.h5", "/ab/treat/scratch/RUNW.h5"),
]

total = 0
diffs = []
for label, pa, pb in PAIRS:
    with h5py.File(pa, "r") as fa, h5py.File(pb, "r") as fb:
        names_a, names_b = [], []

        def collect(acc):
            def cb(name, obj):
                if isinstance(obj, h5py.Dataset):
                    acc.append(name)
            return cb

        fa.visititems(collect(names_a))
        fb.visititems(collect(names_b))
        sa, sb = set(names_a), set(names_b)
        for n in sorted(sa - sb):
            diffs.append((label, n, "only-in-control"))
        for n in sorted(sb - sa):
            diffs.append((label, n, "only-in-treat"))
        for n in sorted(sa & sb):
            total += 1
            da = fa[n][...]
            db = fb[n][...]
            if da.shape != db.shape or da.dtype != db.dtype:
                diffs.append((label, n, "shape/dtype"))
                continue
            if da.dtype == object:
                same = da.tolist() == db.tolist()
            else:
                same = da.tobytes() == db.tobytes()
            if not same:
                diffs.append((label, n, "differs"))

print(f"datasets_compared={total}")
print(f"differing={len(diffs)}")
for label, n, why in diffs:
    print(f"DIFF {label} {n} {why}")
