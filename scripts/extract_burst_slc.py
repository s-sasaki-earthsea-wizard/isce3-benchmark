#!/usr/bin/env python3
"""Extract a single Sentinel-1 burst SLC to a radar-grid raster.

Calls s1reader's `burst.slc_to_vrt_file()` to produce a GDAL VRT pointing at
the burst's slice of the SAFE measurement TIFF. This is essentially free
(no data copy) and gives us a raster object that crossmul can ingest.

Why this exists: COMPASS radar mode coregisters the SECONDARY burst onto
the reference's radar grid (s1_resample.py emits a .slc.tif). The
REFERENCE burst stays in its own grid and is NOT written to disk by
COMPASS. To run crossmul we need both as radar-grid rasters, so we extract
the reference here.

Usage:
  python scripts/extract_burst_slc.py \\
      --safe   /data/S1-data/<ref>.SAFE \\
      --orbit  /data/S1-boso/orbits/<ref>.EOF \\
      --burst-id  t073_153512_iw2 \\
      --pol    VV \\
      --out    /logs/scratch_s1_boso_cslc_cpu/ref_burst.slc.vrt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--safe", required=True, type=Path)
    p.add_argument("--orbit", required=True, type=Path)
    p.add_argument("--burst-id", required=True, type=str)
    p.add_argument("--pol", default="VV")
    p.add_argument("--out", required=True, type=Path,
                   help="output VRT path (parent will be created)")
    return p.parse_args()


def _subswath_index(burst_id: str) -> int:
    """Burst ids look like t073_153512_iw2 — return the trailing IW number."""
    tail = burst_id.split("_")[-1].lower()
    if not tail.startswith("iw") or not tail[2:].isdigit():
        raise SystemExit(f"can't infer subswath from burst_id {burst_id!r}")
    return int(tail[2:])


def main() -> int:
    args = _parse_args()
    from s1reader.s1_reader import load_bursts

    sw = _subswath_index(args.burst_id)
    # s1reader 0.2.5: load_bursts(path, orbit_path, swath_num, pol='vv', ...)
    bursts = load_bursts(str(args.safe), orbit_path=str(args.orbit),
                         swath_num=sw, pol=args.pol.lower())
    for b in bursts:
        if str(b.burst_id) == args.burst_id:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            b.slc_to_vrt_file(str(args.out))
            print(f"[extract_burst_slc] wrote {args.out} for burst {args.burst_id}")
            return 0
    print(f"[extract_burst_slc] burst {args.burst_id} not in {args.safe}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
