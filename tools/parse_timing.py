#!/usr/bin/env python3
"""Aggregate `/usr/bin/time -v` output files from a run directory into CSV.

Each `.time` file in --logs is parsed for wall time, max RSS, CPU%, etc.
Filename convention: `<scenario>_<cpu|gpu>_<repeat>.time`.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

FIELDS = {
    "wall_seconds": re.compile(r"Elapsed \(wall clock\) time.*: (.+)"),
    "max_rss_kb": re.compile(r"Maximum resident set size \(kbytes\): (\d+)"),
    "cpu_percent": re.compile(r"Percent of CPU this job got: (\d+)%"),
    "user_seconds": re.compile(r"User time \(seconds\): ([\d.]+)"),
    "sys_seconds": re.compile(r"System time \(seconds\): ([\d.]+)"),
    "voluntary_cs": re.compile(r"Voluntary context switches: (\d+)"),
    "involuntary_cs": re.compile(r"Involuntary context switches: (\d+)"),
}

NAME_RE = re.compile(r"^(?P<scenario>.+)_(?P<path>cpu|gpu)_(?P<rep>\d+)\.time$")


def hms_to_seconds(s: str) -> float:
    parts = s.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    return parts[0]


def parse_one(path: Path) -> dict:
    text = path.read_text()
    row: dict = {"file": path.name}
    m = NAME_RE.match(path.name)
    if m:
        row.update(m.groupdict())
    for key, pat in FIELDS.items():
        match = pat.search(text)
        if not match:
            continue
        val = match.group(1)
        row[key] = hms_to_seconds(val) if key == "wall_seconds" else val
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True, type=Path,
                    help="Run directory containing *.time files")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output CSV (default: <logs>/timing.csv)")
    args = ap.parse_args()

    if not args.logs.is_dir():
        print(f"not a directory: {args.logs}", file=sys.stderr)
        return 2

    time_files = sorted(args.logs.rglob("*.time"))
    if not time_files:
        print(f"no .time files under {args.logs}", file=sys.stderr)
        return 1

    rows = [parse_one(p) for p in time_files]
    out = args.out or (args.logs / "timing.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = sorted({k for row in rows for k in row.keys()})
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
