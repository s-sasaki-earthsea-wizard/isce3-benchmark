"""Compare two MultiRTC run directories raster by raster (writes JSON to stdout).

Used to check that patched MultiRTC (``patched-matched``: the candidate fix in
patches/multirtc/sicd-rgzero-radar-grid.patch, no runtime subclass) produces
the same geocoded products as the runtime-subclass variant that motivated it
(``matched-tfix-rfix``). For every GeoTIFF in ``<run>/output`` present in both
runs: shape, geotransform and projection equality, NaN-pattern equality, the
fraction of bit-identical pixels and the largest absolute difference. For the
gamma0 raster (``<id>.tif``) and the number of looks, also the distribution of
|10 log10(a / b)| and of |a - b| / b over pixels positive in both.

Usage (inside the dev container)::

    python artifacts/multirtc-sicd-fix-20261108/compare_runs.py \
        /data/capella_mexico_city/multirtc/20240626/patched-matched \
        /data/capella_mexico_city/multirtc/20240626/matched-tfix-rfix
"""
import json
import os
import sys

import numpy as np
from osgeo import gdal


def compare(a_path: str, b_path: str, ratio_stats: bool = False) -> dict:
    a, b = gdal.Open(a_path), gdal.Open(b_path)
    out = {"shape_a": [a.RasterYSize, a.RasterXSize], "shape_b": [b.RasterYSize, b.RasterXSize],
           "same_geotransform": a.GetGeoTransform() == b.GetGeoTransform(),
           "same_projection": a.GetProjection() == b.GetProjection()}
    if out["shape_a"] != out["shape_b"]:
        return out
    x = a.GetRasterBand(1).ReadAsArray().astype(np.float64)
    y = b.GetRasterBand(1).ReadAsArray().astype(np.float64)
    nan_x, nan_y = np.isnan(x), np.isnan(y)
    both = ~nan_x & ~nan_y
    d = np.abs(x[both] - y[both])
    out.update({
        "pixels": int(x.size), "nan_pattern_mismatches": int(np.count_nonzero(nan_x != nan_y)),
        "finite_compared": int(both.sum()),
        "bit_identical_fraction": float(np.mean(x[both] == y[both])) if both.any() else None,
        "max_abs_difference": float(d.max()) if d.size else None,
    })
    if ratio_stats:
        pos = both & (x > 0) & (y > 0)
        db = np.abs(10 * np.log10(x[pos] / y[pos]))
        rel = np.abs(x[pos] - y[pos]) / y[pos]
        p = np.percentile(db, [50, 90, 99, 99.9, 99.99]) if db.size else [None] * 5
        out["positive_compared"] = int(pos.sum())
        out["abs_db"] = {"p50": p[0], "p90": p[1], "p99": p[2], "p99.9": p[3], "p99.99": p[4],
                         "max": float(db.max()) if db.size else None,
                         "frac_lt_0.001db": float(np.mean(db < 0.001)),
                         "frac_lt_0.01db": float(np.mean(db < 0.01)),
                         "count_gt_0.1db": int(np.count_nonzero(db > 0.1))}
        out["relative_difference"] = {"p50": float(np.median(rel)), "p99": float(np.percentile(rel, 99)),
                                      "max": float(rel.max())}
    return out


def main(run_a: str, run_b: str) -> int:
    names_a = sorted(f for f in os.listdir(os.path.join(run_a, "output")) if f.endswith(".tif"))
    names_b = set(f for f in os.listdir(os.path.join(run_b, "output")) if f.endswith(".tif"))
    res = {"run_a": run_a, "run_b": run_b, "only_in_a": sorted(set(names_a) - names_b),
           "only_in_b": sorted(names_b - set(names_a)), "rasters": {}}
    primary = min(names_a, key=len) if names_a else None  # <id>.tif, the gamma0 product
    for n in names_a:
        if n in names_b:
            res["rasters"][n] = compare(os.path.join(run_a, "output", n), os.path.join(run_b, "output", n),
                                        ratio_stats=(n == primary or n.endswith("_number_of_looks.tif")))
    json.dump(res, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
