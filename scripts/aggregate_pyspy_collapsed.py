#!/usr/bin/env python3
"""Aggregate a py-spy raw (collapsed-stacks) profile into a stage table.

Input: the ``pyspy.collapsed.txt`` written by ``run_profile_pyspy.sh`` with
``PYSPY_FORMAT=raw`` — one line per unique stack, ``frame;frame;... count``.

Attribution notes for non ``--native`` profiles:

* A thread blocked inside a pybind11 extension call shows up as the Python
  frame that made the call, so long-running C++ stages appear as a single
  call-site frame (e.g. ``run_geocode_cov (GcovWriter.py:215)`` for the
  GCOV ``geocode()`` call). Use isce3 journal timers to subdivide them.
* py-spy skips idle threads by default, so samples are proportional to
  CPU-active time, not wall time: a stage whose sample share is far below
  its wall share is I/O/wait-bound. Compare against wall-clock stage
  timings before concluding anything.

Usage: aggregate_pyspy_collapsed.py <pyspy.collapsed.txt> [--top N]
"""
import argparse
import re
from collections import Counter

# First matching pattern (checked against every frame in the stack) wins.
STAGE_RULES = [
    ("cpp geocode+RTC (pybind call)", "GcovWriter.py:215)"),
    ("save products (save_dataset)", "save_dataset"),
    ("input prep (prepare_rslc)", "prepare_rslc"),
    ("stats (compute_stats)", "compute_stats"),
    ("h5/metadata writer", "GcovWriter"),
    ("radar grid cubes", "add_radar_grid_cubes"),
    ("imports/startup", "importlib"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("collapsed", help="py-spy raw collapsed-stacks file")
    ap.add_argument("--top", type=int, default=15, help="rows per table")
    args = ap.parse_args()

    total = 0
    stage = Counter()
    call_sites = Counter()
    leaf = Counter()

    with open(args.collapsed) as f:
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
            for fr in stack:
                if "gcov.py:" in fr or "GcovWriter.py:" in fr \
                        or "BaseL2WriterSingleInput.py:" in fr:
                    call_sites[fr] += n

    def show(title, counter, k):
        print(f"\n== {title} ==")
        for name, n in counter.most_common(k):
            print(f"  {100 * n / total:6.2f}%  {n:>9}  {name[:110]}")

    print(f"total samples: {total}")
    show("stage attribution (first matching rule)", stage, len(STAGE_RULES) + 1)
    show("workflow/writer call sites (any depth)", call_sites, args.top)
    show("leaf frames", leaf, args.top)


if __name__ == "__main__":
    main()
