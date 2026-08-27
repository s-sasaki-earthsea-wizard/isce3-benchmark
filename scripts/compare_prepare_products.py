#!/usr/bin/env python3
"""Bitwise dataset-by-dataset comparison of two prepare_insar_hdf5 runs.

Generalized from the 2026-08-16 CPU E2E `compare_bitwise.py`: every
HDF5 dataset in the GUNW product skeleton (`out/product.h5`) and the
RIFG/RUNW scratch skeletons is compared byte-for-byte between two run
directories, each laid out as `<run>/out/product.h5` +
`<run>/scratch/{RIFG,RUNW}.h5`.

Usage:
    python3 compare_prepare_products.py <run_dir_a> <run_dir_b>

Prints one DIFF line per differing dataset. Known run-varying
metadata (processingDateTime, runConfigurationContents) is still
printed — the caller decides what is expected noise.
"""
import sys

import h5py

def dataset_names(f):
    names = []

    def cb(name, obj):
        if isinstance(obj, h5py.Dataset):
            names.append(name)

    f.visititems(cb)
    return names


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    run_a, run_b = sys.argv[1], sys.argv[2]
    pairs = [
        ("GUNW", f"{run_a}/out/product.h5", f"{run_b}/out/product.h5"),
        ("RIFG", f"{run_a}/scratch/RIFG.h5", f"{run_b}/scratch/RIFG.h5"),
        ("RUNW", f"{run_a}/scratch/RUNW.h5", f"{run_b}/scratch/RUNW.h5"),
    ]

    total = 0
    diffs = []
    for label, pa, pb in pairs:
        with h5py.File(pa, "r") as fa, h5py.File(pb, "r") as fb:
            sa, sb = set(dataset_names(fa)), set(dataset_names(fb))
            for n in sorted(sa - sb):
                diffs.append((label, n, "only-in-a"))
            for n in sorted(sb - sa):
                diffs.append((label, n, "only-in-b"))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
