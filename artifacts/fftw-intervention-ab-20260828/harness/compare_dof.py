#!/usr/bin/env python3
"""Compare standalone dense_offsets replicates (bench#36 Step 2, Phase B).

usage: compare_dof.py <tag> [<tag> ...]
Hashes every Ampcor output raster per replicate and, when they differ,
quantifies the difference. Groups are compared within-tag and across-tag.
"""
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

BASE = Path(os.environ.get("STEP2_BASE",
            os.path.expanduser("~/scratch/bench36_step2_20260826")))
OUTS = ["dense_offsets", "gross_offsets", "snr", "covariance",
        "correlation_peak"]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def read(p):
    ds = gdal.Open(str(p))
    return np.stack([ds.GetRasterBand(i + 1).ReadAsArray()
                     for i in range(ds.RasterCount)])


def rep_dirs(tag):
    ds = sorted(BASE.glob(f"dof_{tag}_*"),
                key=lambda p: int(p.name.rsplit("_", 1)[1]))
    return [d / "scratch" / "dense_offsets" / "freqA" / "HH" for d in ds]


tags = sys.argv[1:] or ["base"]
groups = {t: rep_dirs(t) for t in tags}
for t, ds in groups.items():
    print(f"{t}: {len(ds)} replicate(s)")

print("\n# sha256 of Ampcor outputs")
table = {}
for t, ds in groups.items():
    for i, d in enumerate(ds, 1):
        for o in OUTS:
            p = d / o
            table[(t, i, o)] = sha256_file(p) if p.exists() else None

for o in OUTS:
    line = []
    for t, ds in groups.items():
        hs = [table[(t, i, o)] for i in range(1, len(ds) + 1)]
        uniq = len({h for h in hs if h})
        line.append(f"{t}:{uniq}uniq/{len(hs)}")
    print(f"  {o:18s} " + "  ".join(line))

print("\n# within-group detail (replicate 1 as reference)")
for t, ds in groups.items():
    if len(ds) < 2:
        continue
    print(f"\n## {t}")
    for o in OUTS:
        a = read(ds[0] / o)
        for i, d in enumerate(ds[1:], 2):
            b = read(d / o)
            if a.tobytes() == b.tobytes():
                print(f"  {o:18s} rep1 vs rep{i}: identical")
            else:
                diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
                n = np.count_nonzero(diff)
                print(f"  {o:18s} rep1 vs rep{i}: DIFFERS "
                      f"n={n}/{diff.size} ({n/diff.size:.2e}) "
                      f"max={np.nanmax(diff):.3e}")

if len(groups) > 1:
    print("\n# across-group (rep1 of each tag vs rep1 of first tag)")
    ts = list(groups)
    ref_d = groups[ts[0]][0]
    for t in ts[1:]:
        print(f"\n## {ts[0]} vs {t}")
        for o in OUTS:
            a, b = read(ref_d / o), read(groups[t][0] / o)
            if a.tobytes() == b.tobytes():
                print(f"  {o:18s}: identical")
            else:
                diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
                n = np.count_nonzero(diff)
                print(f"  {o:18s}: DIFFERS n={n}/{diff.size} "
                      f"({n/diff.size:.2e}) max={np.nanmax(diff):.3e}")
