#!/usr/bin/env python3
"""Stitch a Copernicus GLO-30 DEM GeoTIFF over a lon/lat bounding box.

Same mechanism as ``fetch/fetch_sentinel1.py`` (``dem_stitcher``), split out so
non-Sentinel stacks -- here the Capella Mexico City SICD pair (bench#54) -- can
stage a DEM from a bbox alone. Heights are metres above the WGS84 ellipsoid,
which is what isce3 expects.

Usage::

    python fetch/fetch_dem_bbox.py --bbox W S E N --out /data/capella_mexico_city/dem.tif [--dem glo_30]

Prints one JSON line with the bbox, shape, and height statistics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dem", default="glo_30", help="dem_stitcher key (default glo_30)")
    args = ap.parse_args()

    from dem_stitcher.stitcher import stitch_dem
    import numpy as np
    import rasterio

    args.out.parent.mkdir(parents=True, exist_ok=True)
    arr, profile = stitch_dem(bounds=list(args.bbox), dem_name=args.dem,
                              dst_ellipsoidal_height=True, dst_area_or_point="Point")
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(arr, 1)
    valid = np.isfinite(arr)
    print(json.dumps({
        "out": str(args.out), "dem": args.dem, "bbox": args.bbox,
        "shape": list(arr.shape), "crs": str(profile.get("crs")),
        "height_min": float(np.nanmin(arr)), "height_max": float(np.nanmax(arr)),
        "height_mean": float(np.nanmean(arr)), "nodata_fraction": float(1 - valid.mean()),
        "ellipsoidal": True,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
