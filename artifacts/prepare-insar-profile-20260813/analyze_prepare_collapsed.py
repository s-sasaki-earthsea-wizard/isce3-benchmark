#!/usr/bin/env python3
"""Aggregate the prepare_insar_hdf5 py-spy raw profile into attribution tables.

Input: py-spy raw (collapsed-stacks) output, plain or gzipped —
one line per unique stack, ``frame;frame;... count``.

Emits three tables:
  1. per-function stage attribution (first matching rule wins per stack),
  2. (product writer, swaths group, function) decomposition,
  3. top leaf frames.

Attribution notes (non ``--native`` profile):
  * A thread blocked inside a pybind11 extension call shows up as the
    Python call-site frame, so C++/CUDA stages appear as a single frame
    (e.g. ``generate_dem_rdr`` for the GPU topo call).
  * py-spy skips idle threads: samples are proportional to CPU-active
    time, not wall time. Compare with the journal wall clock.

Usage: analyze_prepare_collapsed.py <pyspy.collapsed.txt[.gz]> [--top N]
"""
import argparse
import gzip
import re
from collections import Counter

STAGE_RULES = [
    ("generate_insar_mask", "generate_insar_mask"),
    ("generate_dem_rdr (topo interp)", "generate_dem_rdr"),
    ("save_to_hdf5_ds (dem rdr->h5)", "save_to_hdf5_ds"),
    ("compute_stats", "compute_stats"),
    ("geolocation grid cubes (L1)", "add_geolocation_grid_cubes"),
    ("radar grid cubes (L2)", "add_radar_grid_cubes"),
    ("imports/startup", "importlib"),
]

PRODUCTS = ("RIFG", "RUNW", "GUNW", "ROFF", "GOFF")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("collapsed", help="py-spy raw collapsed-stacks file (.gz ok)")
    ap.add_argument("--top", type=int, default=15, help="rows in leaf table")
    args = ap.parse_args()

    opener = gzip.open if args.collapsed.endswith(".gz") else open
    total = 0
    stage = Counter()
    combo = Counter()
    leaf = Counter()

    with opener(args.collapsed, "rt") as f:
        for line in f:
            m = re.match(r"^(.*) (\d+)$", line.rstrip("\n"))
            if not m:
                continue
            stack, n = m.group(1).split(";"), int(m.group(2))
            total += n
            leaf[stack[-1]] += n
            for name, pat in STAGE_RULES:
                if any(pat in fr for fr in stack):
                    stage[name] += n
                    break
            else:
                stage["other"] += n
            prod = next((p for p in PRODUCTS
                         if any(f"{p}_writer" in fr for fr in stack)), "?")
            group = ("mask:offsets" if any("add_pixel_offsets_to_swaths_group" in fr
                                           for fr in stack)
                     else "mask:igram" if any("add_interferogram_to_swaths_group" in fr
                                              for fr in stack)
                     else "other")
            func = next((name for name, pat in STAGE_RULES[:4]
                         if any(pat in fr for fr in stack)), "misc")
            combo[(prod, group, func)] += n

    print(f"TOTAL samples: {total}")
    print("\n== stage attribution ==")
    for k, v in stage.most_common():
        print(f"{v:7d} {100 * v / total:5.1f}%  {k}")
    print("\n== (product writer, group, function) ==")
    for k, v in combo.most_common(20):
        print(f"{v:7d} {100 * v / total:5.1f}%  {k}")
    print(f"\n== top {args.top} leaf frames ==")
    for k, v in leaf.most_common(args.top):
        print(f"{v:7d} {100 * v / total:5.1f}%  {k[:130]}")


if __name__ == "__main__":
    main()
