#!/usr/bin/env python3
"""Build a minimal COMPASS burst database for the selected Boso burst.

COMPASS's `GeoRunConfig.load_from_yaml` requires a real
`burst_database_file` (the `None` branch is dead code — the early
`os.path.isfile()` check rejects None with a TypeError). The full
OPERA-JPL burst map is ~hundreds of MB and intended to be globally
consistent across OPERA products. For profile purposes we only need
the one burst we're geocoding, with bbox values that produce a sensible
geogrid — production-grid consistency is irrelevant here.

This builder writes a SQLite file matching the schema queried by
`compass.utils.helpers.burst_bboxes_from_db`:

    CREATE TABLE burst_id_map (
        burst_id_jpl TEXT,
        epsg         INTEGER,
        xmin REAL, ymin REAL, xmax REAL, ymax REAL
    );

The bbox is taken from `bursts.json` (already in lat/lon, EPSG:4326) and
the EPSG is set to 4326 so it matches the DEM CRS — that path skips
re-projection inside `generate_geogrids_from_db`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bursts", required=True, type=Path,
                   help="bursts.json produced by fetch_sentinel1.py")
    p.add_argument("--burst-id", required=True,
                   help="JPL burst ID to include (e.g. t046_097519_iw3)")
    p.add_argument("--pol", default="VV",
                   help="polarization filter for picking the bbox row (default: VV)")
    p.add_argument("--out", required=True, type=Path,
                   help="output sqlite path (will be overwritten)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    records = json.loads(args.bursts.read_text())

    matches = [r for r in records
               if r["burst_id"] == args.burst_id and r["polarization"] == args.pol]
    if not matches:
        raise SystemExit(f"no record for burst_id={args.burst_id} pol={args.pol} in {args.bursts}")

    # Any matching record's bbox will do — across SAFE files the burst bbox
    # is the same geographic footprint (timing differs, footprint doesn't).
    xmin, ymin, xmax, ymax = matches[0]["bbox_lonlat"]
    epsg = 4326

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    with sqlite3.connect(args.out) as conn:
        conn.execute("""
            CREATE TABLE burst_id_map (
                burst_id_jpl TEXT PRIMARY KEY,
                epsg INTEGER,
                xmin REAL,
                ymin REAL,
                xmax REAL,
                ymax REAL
            )
        """)
        conn.execute(
            "INSERT INTO burst_id_map (burst_id_jpl, epsg, xmin, ymin, xmax, ymax) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.burst_id, epsg, xmin, ymin, xmax, ymax),
        )

    print(f"[burst-db] wrote {args.out}")
    print(f"[burst-db]   burst_id={args.burst_id} epsg={epsg}")
    print(f"[burst-db]   bbox=({xmin:.5f}, {ymin:.5f}, {xmax:.5f}, {ymax:.5f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
