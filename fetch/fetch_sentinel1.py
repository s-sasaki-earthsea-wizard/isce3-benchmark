#!/usr/bin/env python3
"""Fetch ancillaries (POEORB orbits + DEM) for a Sentinel-1 SAFE pair.

This script does NOT download SAFE files themselves — those are expected to
be staged by the user under ``data/S1-data/<scene>.SAFE/``. It only fills in
the ancillaries that COMPASS radar mode needs:

  - precise (POEORB) orbit EOF files, one per SAFE, via ``sentineleof``
  - a stitched DEM in EPSG:4326 covering the union of burst footprints,
    via ``dem_stitcher`` (Copernicus GLO-30 by default)

It also enumerates bursts via ``s1reader`` so the caller can pick a
``burst_id`` for the runconfig.

Outputs (under ``OUT_DIR``, default ``data/S1-boso/``):
  orbits/<eof_files>.EOF
  dem.tif
  bursts.json   # list of all bursts in both SAFEs with metadata

Usage:
  python fetch/fetch_sentinel1.py \\
      --safe data/S1-data/S1A_IW_SLC__..._CC6C.SAFE \\
      --safe data/S1-data/S1A_IW_SLC__..._C319.SAFE \\
      --out  data/S1-boso

Environment:
  COPERNICUS_USER, COPERNICUS_PASS  -- optional, sentineleof can use anonymous
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--safe", action="append", required=True, dest="safes",
                   help="path to a SAFE directory; pass twice for a pair")
    p.add_argument("--out", required=True, type=Path,
                   help="output directory for orbits/, dem.tif, bursts.json")
    p.add_argument("--dem", default="glo_30",
                   help="dem_stitcher key (default: glo_30 = Copernicus GLO-30)")
    p.add_argument("--margin-deg", type=float, default=0.05,
                   help="latitude/longitude padding around burst union (deg)")
    p.add_argument("--skip-orbits", action="store_true",
                   help="do not download POEORB EOF files")
    p.add_argument("--skip-dem", action="store_true",
                   help="do not stitch a DEM")
    return p.parse_args()


def _enumerate_bursts(safes: list[Path]) -> list[dict]:
    """Use s1reader to list bursts in each SAFE. Returns one record per burst."""
    from s1reader.s1_reader import load_bursts

    records: list[dict] = []
    for safe in safes:
        for subswath in (1, 2, 3):
            for pol in ("VV", "VH"):
                try:
                    bursts = load_bursts(str(safe), orbit_path=None,
                                         i_subswath=subswath, pol=pol)
                except Exception as e:
                    print(f"[fetch] {safe.name} IW{subswath} {pol}: skip ({type(e).__name__}: {e})",
                          file=sys.stderr)
                    continue
                for b in bursts:
                    poly = getattr(b, "border", None)
                    bbox = list(poly[0].bounds) if poly else None
                    records.append({
                        "safe": safe.name,
                        "burst_id": str(b.burst_id),
                        "subswath": f"IW{subswath}",
                        "polarization": pol,
                        "sensing_start": b.sensing_start.isoformat() if b.sensing_start else None,
                        "bbox_lonlat": bbox,
                    })
    return records


def _union_bbox(bursts: list[dict], margin: float) -> tuple[float, float, float, float]:
    bboxes = [b["bbox_lonlat"] for b in bursts if b.get("bbox_lonlat")]
    if not bboxes:
        raise RuntimeError("no burst footprints available to compute DEM bbox")
    minx = min(b[0] for b in bboxes) - margin
    miny = min(b[1] for b in bboxes) - margin
    maxx = max(b[2] for b in bboxes) + margin
    maxy = max(b[3] for b in bboxes) + margin
    return minx, miny, maxx, maxy


def _fetch_orbits(safes: list[Path], out_dir: Path) -> None:
    """Download POEORB EOF files via sentineleof."""
    from eof.download import download_eofs

    out_dir.mkdir(parents=True, exist_ok=True)
    for safe in safes:
        print(f"[fetch] orbit for {safe.name} -> {out_dir}")
        download_eofs(sentinel_file=str(safe), save_dir=str(out_dir))


def _fetch_dem(bbox: tuple[float, float, float, float], out: Path, dem_key: str) -> None:
    """Stitch a DEM tile covering bbox via dem_stitcher."""
    from dem_stitcher.stitcher import stitch_dem
    import rasterio

    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] DEM '{dem_key}' bbox={bbox} -> {out}")
    arr, profile = stitch_dem(bounds=list(bbox), dem_name=dem_key)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr, 1)


def main() -> int:
    args = _parse_args()
    safes = [Path(p).resolve() for p in args.safes]
    for safe in safes:
        if not safe.is_dir():
            print(f"[fetch] error: {safe} is not a directory", file=sys.stderr)
            return 2

    args.out.mkdir(parents=True, exist_ok=True)

    # 1. enumerate bursts (also seeds the DEM bbox)
    print(f"[fetch] enumerating bursts across {len(safes)} SAFE files...")
    bursts = _enumerate_bursts(safes)
    print(f"[fetch] found {len(bursts)} burst records")
    bursts_json = args.out / "bursts.json"
    bursts_json.write_text(json.dumps(bursts, indent=2))
    print(f"[fetch] wrote {bursts_json}")

    # 2. orbits
    if not args.skip_orbits:
        _fetch_orbits(safes, args.out / "orbits")

    # 3. DEM
    if not args.skip_dem:
        bbox = _union_bbox(bursts, margin=args.margin_deg)
        _fetch_dem(bbox, args.out / "dem.tif", args.dem)

    print("[fetch] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
