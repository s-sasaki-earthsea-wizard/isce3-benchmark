#!/usr/bin/env python3
"""List validMask datasets in prepare_insar_hdf5 skeletons: shape, dtype,
value histogram, and the co-located mask dataset for cross-reference.

Usage: python3 inspect_validmask.py <run_dir>
"""
import sys
import numpy as np
import h5py

def main():
    run = sys.argv[1]
    for label, path in [("GUNW", f"{run}/out/product.h5"),
                        ("RIFG", f"{run}/scratch/RIFG.h5"),
                        ("RUNW", f"{run}/scratch/RUNW.h5")]:
        with h5py.File(path, "r") as f:
            hits = []
            f.visititems(lambda n, o: hits.append(n)
                         if isinstance(o, h5py.Dataset)
                         and n.endswith("validMask") else None)
            for n in sorted(hits):
                d = f[n][...]
                vals, counts = np.unique(d, return_counts=True)
                hist = ", ".join(f"{v}:{c}" for v, c in zip(vals, counts))
                print(f"{label} /{n} shape={d.shape} dtype={d.dtype} values={{{hist}}}")
            # subswath mask datasets for cross-reference
            masks = []
            f.visititems(lambda n, o: masks.append(n)
                         if isinstance(o, h5py.Dataset)
                         and n.endswith("/mask") else None)
            for n in sorted(masks):
                d = f[n][...]
                nz = int(np.count_nonzero(d))
                print(f"{label} /{n} shape={d.shape} dtype={d.dtype} nonzero={nz}/{d.size}")

if __name__ == "__main__":
    main()
