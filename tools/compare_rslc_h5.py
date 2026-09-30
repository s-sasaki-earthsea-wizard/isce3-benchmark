#!/usr/bin/env python3
"""Compare two RSLC HDF5 files dataset by dataset (values, dtype, shape, attributes).

Used to show that the isce3 example ``share/nisar/examples/sicd_to_nisar_rslc.py``
(fork branch ``feat/sicd-rgzero-to-rslc``) writes the same product as the bench
converter ``tools/sicd_to_nisar_rslc.py``. Differences are listed per path and
classified as EXPECTED when the path (or ``path@attribute``) is in the allow-list
given with ``--expect``; anything else is UNEXPECTED and makes the exit status 1.

Values are compared exactly (``numpy.array_equal`` with NaN equal); the image
dataset is compared in row blocks so memory stays bounded.

Usage::

    python tools/compare_rslc_h5.py bench.h5 port.h5 \\
        --expect science/LSAR/identification/processingDateTime ... [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys

import h5py
import numpy as np


def _walk(f):
    out = {}
    f.visititems(lambda n, o: out.__setitem__(n, o) if isinstance(o, h5py.Dataset) else None)
    return out


def _equal(a, b):
    if a.dtype.kind in "fc" or (a.dtype.names is not None):
        if a.dtype.names is not None:
            return all(np.array_equal(a[n], b[n], equal_nan=True) for n in a.dtype.names)
        return np.array_equal(a, b, equal_nan=True)
    return np.array_equal(a, b)


def _attrs_equal(x, y):
    xa, ya = dict(x.attrs), dict(y.attrs)
    diffs = []
    for k in sorted(set(xa) | set(ya)):
        if k not in xa or k not in ya:
            diffs.append((k, "missing on one side"))
        elif not np.array_equal(np.asarray(xa[k]), np.asarray(ya[k])):
            diffs.append((k, "value differs"))
    return diffs


def compare(path_a, path_b, block=1024):
    diffs = []
    with h5py.File(path_a, "r") as fa, h5py.File(path_b, "r") as fb:
        da, db = _walk(fa), _walk(fb)
        for p in sorted(set(da) - set(db)):
            diffs.append({"path": p, "kind": "only in first"})
        for p in sorted(set(db) - set(da)):
            diffs.append({"path": p, "kind": "only in second"})
        n_compared = 0
        for p in sorted(set(da) & set(db)):
            a, b = da[p], db[p]
            n_compared += 1
            if a.shape != b.shape or a.dtype != b.dtype:
                diffs.append({"path": p, "kind": "shape/dtype",
                              "detail": f"{a.shape} {a.dtype} vs {b.shape} {b.dtype}"})
                continue
            if a.ndim >= 2 and a.shape[0] > block:
                same = all(_equal(a[i:i + block], b[i:i + block])
                           for i in range(0, a.shape[0], block))
            else:
                same = _equal(a[()], b[()]) if a.shape != () else _equal(np.asarray(a[()]), np.asarray(b[()]))
            if not same:
                diffs.append({"path": p, "kind": "value"})
            for k, why in _attrs_equal(a, b):
                diffs.append({"path": f"{p}@{k}", "kind": f"attribute {why}"})
        for k, why in _attrs_equal(fa, fb):
            diffs.append({"path": f"/@{k}", "kind": f"attribute {why}"})
    return n_compared, diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("first")
    ap.add_argument("second")
    ap.add_argument("--expect", nargs="*", default=[],
                    help="paths (or path@attribute) whose difference is expected")
    ap.add_argument("--json", help="write the result here")
    args = ap.parse_args(argv)
    n, diffs = compare(args.first, args.second)
    for d in diffs:
        d["class"] = "EXPECTED" if d["path"] in args.expect else "UNEXPECTED"
    unexpected = [d for d in diffs if d["class"] == "UNEXPECTED"]
    result = {"first": args.first, "second": args.second, "datasets_compared": n,
              "differences": diffs, "unexpected": len(unexpected),
              "verdict": "IDENTICAL except expected" if not unexpected else "DIFFERENT"}
    for d in diffs:
        print(f"{d['class']:10s} {d['kind']:28s} {d['path']}")
    print(f"{n} datasets compared, {len(diffs)} differences, {len(unexpected)} unexpected: "
          f"{result['verdict']}")
    if args.json:
        json.dump(result, open(args.json, "w"), indent=1)
    return 0 if not unexpected else 1


if __name__ == "__main__":
    sys.exit(main())
