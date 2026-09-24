#!/usr/bin/env python3
"""Compare isce3 GCOV (our SICD RSLC) with a MultiRTC run on the same SICD (bench#54).

Both paths call the same isce3 geocode / RTC core with matched options (see
configs/gcov_capella_mexico_city_20240626.yaml and tools/multirtc_capella_rtc.py),
so what this measures is the **ingest / packaging** difference between the two
paths, not two RTC algorithms, and nothing here says anything about InSAR phase.

Two stages, in the order that lets differences be attributed:

1. Radar domain -- before any geocoding:
   * calibrated pixels: MultiRTC's complex beta0 GeoTIFF (``input/<stem>_beta0.tif``)
     vs the beta0 image in our RSLC (same orientation expected);
   * radar grid: absolute sensing start (in lines), starting range (in
     samples), PRF, wavelength, and orbit positions at common times;
   * the map offset those grid differences alone imply, by rdr2geo of 25
     pixels through both grids (same orbit, constant height).
2. Geocoded gamma0, on the intersection of the two 5 m grids:
   * support: finite and > 0 on each side (GCOV additionally mask in 1..254);
     common / GCOV-only / MultiRTC-only pixel counts;
   * radiometry: 10 log10(MultiRTC / GCOV) on the common support;
   * geometry: sub-pixel offset of MultiRTC relative to GCOV from phase
     correlation of 256 x 256 dB tiles (Hann window, parabolic peak), reported
     in pixels, metres east/north and along/across the flight direction.

Usage (inside the dev container)::

    python tools/compare_rtc.py --gcov gcov.h5 --rslc rslc_beta0.h5 \\
        --multirtc-run /data/.../multirtc/20240626/matched --out cmp.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import h5py
import numpy as np

_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
from nisar.products.readers import SLC  # noqa: E402
sys.argv = _ARGV
from osgeo import gdal  # noqa: E402

GCOV_GRID = "science/LSAR/GCOV/grids/frequencyA"


# ---------------------------------------------------------------- radar domain
def radar_pixels(rslc_path: str, beta0_tif: str, block: int = 1024) -> dict:
    ds = gdal.Open(beta0_tif)
    band = ds.GetRasterBand(1)
    with h5py.File(rslc_path) as f:
        img = f["science/LSAR/RSLC/swaths/frequencyA/HH"]
        n_az, n_rg = img.shape
        if (ds.RasterYSize, ds.RasterXSize) != (n_az, n_rg):
            return {"error": f"shape mismatch: tif {ds.RasterYSize}x{ds.RasterXSize} vs rslc {n_az}x{n_rg}"}
        max_rel = 0.0
        max_phase = 0.0
        n_cmp = 0
        n_zero_mismatch = 0
        for l0 in range(0, n_az, block):
            l1 = min(l0 + block, n_az)
            a = img[l0:l1].astype(np.complex128)
            b = band.ReadAsArray(0, l0, n_rg, l1 - l0).astype(np.complex128)
            nz = (a != 0) & (b != 0)
            n_zero_mismatch += int(((a == 0) != (b == 0)).sum())
            if nz.any():
                max_rel = max(max_rel, float(np.max(np.abs(b[nz] - a[nz]) / np.abs(a[nz]))))
                max_phase = max(max_phase, float(np.max(np.abs(np.angle(b[nz] * np.conj(a[nz]))))))
                n_cmp += int(nz.sum())
    return {"pixels_compared": n_cmp, "max_relative_difference": max_rel,
            "max_abs_phase_difference_rad": max_phase, "zero_pattern_mismatches": n_zero_mismatch}


def radar_grid(rslc_path: str, run_json: str) -> dict:
    run = json.load(open(run_json))
    mg = run["grid"]
    slc = SLC(hdf5file=rslc_path)
    g = slc.getRadarGrid()
    orbit = slc.getOrbit()
    # absolute start times as seconds since our epoch
    m_epoch = isce3.core.DateTime(mg["ref_epoch"])
    m_start = mg["sensing_start_s"] + (m_epoch - g.ref_epoch).total_seconds()
    dt = 1.0 / g.prf
    pos_diff = []
    for s in mg["orbit_samples"]:
        t_ours = s["t"] + (m_epoch - g.ref_epoch).total_seconds()
        p, _ = orbit.interpolate(t_ours)
        pos_diff.append(float(np.linalg.norm(np.asarray(p) - np.asarray(s["pos"]))))
    return {
        "sensing_start_minus_ours_lines": (m_start - g.sensing_start) / dt,
        "sensing_start_minus_ours_s": m_start - g.sensing_start,
        "starting_range_minus_ours_m": mg["starting_range_m"] - g.starting_range,
        "starting_range_minus_ours_samples": (mg["starting_range_m"] - g.starting_range) / g.range_pixel_spacing,
        "prf_ratio": mg["prf_hz"] / g.prf,
        "wavelength_multirtc_m": mg["wavelength_m"], "wavelength_ours_m": g.wavelength,
        "shape_multirtc": [mg["length"], mg["width"]], "shape_ours": [g.length, g.width],
        "orbit_position_difference_m_max": max(pos_diff),
    }


def predicted_offset(rslc_path: str, run_json: str, height: float | None = None) -> dict:
    """Where MultiRTC's radar grid puts a pixel minus where ours does, by rdr2geo.

    Exact geometry for the grid difference alone (same orbit, same constant
    height surface), to compare with the correlation-measured map offset.
    """
    run = json.load(open(run_json))
    mg = run["grid"]
    slc = SLC(hdf5file=rslc_path)
    g = slc.getRadarGrid()
    orbit = slc.getOrbit()
    m_start = mg["sensing_start_s"] + (isce3.core.DateTime(mg["ref_epoch"]) - g.ref_epoch).total_seconds()
    if height is None:
        with h5py.File(rslc_path) as f:
            poly = f["science/LSAR/identification/boundingPolygon"][()].decode()
        height = float(poly.split("((")[1].split(",")[0].split()[2])
    dem = isce3.geometry.DEMInterpolator(height)
    at, ct, _ = flight_frame(rslc_path, 0)
    ell = isce3.core.Ellipsoid()
    along, across = [], []
    for line in np.linspace(0, g.length - 1, 5):
        for samp in np.linspace(0, g.width - 1, 5):
            t_o = g.sensing_start + line / g.prf
            r_o = g.starting_range + samp * g.range_pixel_spacing
            t_m = m_start + line / mg["prf_hz"]
            r_m = mg["starting_range_m"] + samp * mg["range_pixel_spacing_m"]
            xo = np.asarray(isce3.geometry.rdr2geo_bracket(t_o, r_o, orbit, g.lookside, 0.0, g.wavelength, dem))
            xm = np.asarray(isce3.geometry.rdr2geo_bracket(t_m, r_m, orbit, g.lookside, 0.0, g.wavelength, dem))
            lon, lat, _ = ell.xyz_to_lon_lat(xo)
            east = np.array([-np.sin(lon), np.cos(lon), 0.0])
            north = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
            d = xm - xo
            de, dn = float(d @ east), float(d @ north)
            along.append(de * at[0] + dn * at[1])
            across.append(de * ct[0] + dn * ct[1])
    return {"height_m": height, "points": 25,
            "median_along_track_m": float(np.median(along)),
            "median_across_track_far_positive_m": float(np.median(across)),
            "range_along_m": [float(min(along)), float(max(along))],
            "range_across_m": [float(min(across)), float(max(across))]}


# ---------------------------------------------------------------- geocoded
def read_gcov(path: str):
    with h5py.File(path) as f:
        g = f[GCOV_GRID]
        out = {"gamma0": g["HHHH"][()], "x": g["xCoordinates"][()], "y": g["yCoordinates"][()],
               "mask": g["mask"][()], "nlooks": g["numberOfLooks"][()],
               "anf": g["rtcAreaNormalizationFactor"][()] if "rtcAreaNormalizationFactor" in g else None}
    return out


def read_tif(path: str):
    ds = gdal.Open(path)
    a = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    x = gt[0] + gt[1] * (np.arange(ds.RasterXSize) + 0.5)
    y = gt[3] + gt[5] * (np.arange(ds.RasterYSize) + 0.5)
    return a, x, y


def _overlap(xa, ya, xb, yb):
    dx = xa[1] - xa[0]
    dy = ya[1] - ya[0]
    for name, d, a, b in (("x", dx, xa, xb), ("y", dy, ya, yb)):
        off = (b[0] - a[0]) / d
        if abs(off - round(off)) > 1e-6 or abs((b[1] - b[0]) - d) > 1e-9:
            raise SystemExit(f"grids not co-aligned in {name}: offset {off} px")
    ix0 = max(0, int(round((xb[0] - xa[0]) / dx)))
    iy0 = max(0, int(round((yb[0] - ya[0]) / dy)))
    jx0 = max(0, int(round((xa[0] - xb[0]) / dx)))
    jy0 = max(0, int(round((ya[0] - yb[0]) / dy)))
    nx = min(len(xa) - ix0, len(xb) - jx0)
    ny = min(len(ya) - iy0, len(yb) - jy0)
    return (slice(iy0, iy0 + ny), slice(ix0, ix0 + nx)), (slice(jy0, jy0 + ny), slice(jx0, jx0 + nx))


def _phase_corr_shift(a: np.ndarray, b: np.ndarray):
    """Shift (dy, dx) in pixels that best maps a onto b (b(x) ~ a(x - shift))."""
    w = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa = np.fft.fft2((a - a.mean()) * w)
    fb = np.fft.fft2((b - b.mean()) * w)
    r = fb * np.conj(fa)
    r /= np.maximum(np.abs(r), 1e-12)
    c = np.real(np.fft.ifft2(r))
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    ny, nx = c.shape

    def para(cm, c0, cp):
        den = cm - 2 * c0 + cp
        return 0.0 if den == 0 else 0.5 * (cm - cp) / den

    dy = iy + para(c[(iy - 1) % ny, ix], c[iy, ix], c[(iy + 1) % ny, ix])
    dx = ix + para(c[iy, (ix - 1) % nx], c[iy, ix], c[iy, (ix + 1) % nx])
    if dy > ny / 2:
        dy -= ny
    if dx > nx / 2:
        dx -= nx
    return dy, dx, float(c[iy, ix])


def flight_frame(rslc_path: str, epsg: int):
    """Unit vectors of the flight direction (along-track) projected into the output map (E, N)."""
    slc = SLC(hdf5file=rslc_path)
    g = slc.getRadarGrid()
    orbit = slc.getOrbit()
    t = g.sensing_mid
    p, v = orbit.interpolate(t)
    ell = isce3.core.Ellipsoid()
    llh = ell.xyz_to_lon_lat(np.asarray(p))
    lon, lat = llh[0], llh[1]
    east = np.array([-np.sin(lon), np.cos(lon), 0.0])
    north = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
    ve, vn = float(np.dot(v, east)), float(np.dot(v, north))
    at = np.array([ve, vn]) / np.hypot(ve, vn)
    look = str(g.lookside).lower()
    # across-track pointing away from the radar (towards far range)
    ct = np.array([-at[1], at[0]]) if "left" in look else np.array([at[1], -at[0]])
    return at, ct, float(np.degrees(np.arctan2(ve, vn)))


def geocoded(gcov_path: str, run_dir: str, rslc_path: str, tile: int = 256) -> dict:
    gc = read_gcov(gcov_path)
    tifs = sorted(glob.glob(os.path.join(run_dir, "output", "*.tif")))
    main = [t for t in tifs if not any(k in os.path.basename(t) for k in
                                       ("number_of_looks", "rtc_anf", "mask", "incidence",
                                        "projection", "slope", "dem"))]
    if len(main) != 1:
        raise SystemExit(f"could not identify the MultiRTC gamma0 raster among {tifs}")
    mr, xm, ym = read_tif(main[0])
    sg, sm = _overlap(gc["x"], gc["y"], xm, ym)
    a = gc["gamma0"][sg].astype(np.float64)
    b = mr[sm].astype(np.float64)
    ga = np.isfinite(a) & (a > 0) & (gc["mask"][sg] >= 1) & (gc["mask"][sg] <= 254)
    gb = np.isfinite(b) & (b > 0)
    both = ga & gb
    d_db = 10 * np.log10(b[both] / a[both])
    out = {
        "multirtc_raster": os.path.basename(main[0]),
        "overlap_shape": list(a.shape),
        "support": {"common": int(both.sum()), "gcov_only": int((ga & ~gb).sum()),
                    "multirtc_only": int((gb & ~ga).sum())},
        "radiometry_db": {"median": float(np.median(d_db)), "p05": float(np.percentile(d_db, 5)),
                          "p95": float(np.percentile(d_db, 95)),
                          "mad": float(np.median(np.abs(d_db - np.median(d_db)))),
                          "frac_abs_lt_0.01db": float(np.mean(np.abs(d_db) < 0.01)),
                          "frac_abs_lt_0.1db": float(np.mean(np.abs(d_db) < 0.1))},
    }
    # tile phase correlation on dB images
    adb = np.where(ga, 10 * np.log10(np.where(ga, a, 1.0)), np.nan)
    bdb = np.where(gb, 10 * np.log10(np.where(gb, b, 1.0)), np.nan)
    shifts = []
    ny, nx = a.shape
    for y0 in range(0, ny - tile + 1, tile):
        for x0 in range(0, nx - tile + 1, tile):
            ta = adb[y0:y0 + tile, x0:x0 + tile]
            tb = bdb[y0:y0 + tile, x0:x0 + tile]
            ok = np.isfinite(ta) & np.isfinite(tb)
            if ok.mean() < 0.95:
                continue
            ta = np.clip(np.where(ok, ta, np.nanmean(ta)), -30, 10)
            tb = np.clip(np.where(ok, tb, np.nanmean(tb)), -30, 10)
            dy, dx, peak = _phase_corr_shift(ta, tb)
            shifts.append((dy, dx, peak))
    if shifts:
        s = np.array(shifts)
        dxs, dys = gc["x"][1] - gc["x"][0], gc["y"][1] - gc["y"][0]
        de, dn = s[:, 1] * dxs, s[:, 0] * dys           # metres east / north
        at, ct, heading = flight_frame(rslc_path, 0)
        along = de * at[0] + dn * at[1]
        across = de * ct[0] + dn * ct[1]
        out["offset_multirtc_minus_gcov"] = {
            "tiles": int(len(s)), "tile_px": tile,
            "median_px_x": float(np.median(s[:, 1])), "median_px_y": float(np.median(s[:, 0])),
            "median_east_m": float(np.median(de)), "median_north_m": float(np.median(dn)),
            "median_along_track_m": float(np.median(along)),
            "median_across_track_far_positive_m": float(np.median(across)),
            "mad_along_m": float(np.median(np.abs(along - np.median(along)))),
            "mad_across_m": float(np.median(np.abs(across - np.median(across)))),
            "median_peak": float(np.median(s[:, 2])),
            "flight_heading_deg_from_north": heading,
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gcov", required=True)
    ap.add_argument("--rslc", required=True, help="the beta0 RSLC GCOV was run on")
    ap.add_argument("--multirtc-run", required=True, help="work dir written by multirtc_capella_rtc.py rtc")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-pixels", action="store_true", help="skip the radar-domain pixel comparison")
    args = ap.parse_args(argv)
    run_json = os.path.join(args.multirtc_run, "run.json")
    res = {"gcov": args.gcov, "rslc": args.rslc, "multirtc_run": args.multirtc_run,
           "variant": json.load(open(run_json))["variant"]}
    res["radar_grid"] = radar_grid(args.rslc, run_json)
    res["predicted_offset_from_grid"] = predicted_offset(args.rslc, run_json)
    if not args.skip_pixels:
        beta0 = glob.glob(os.path.join(args.multirtc_run, "input", "*_beta0.tif"))
        res["radar_pixels"] = radar_pixels(args.rslc, beta0[0]) if beta0 else {"error": "no beta0 tif"}
    res["geocoded"] = geocoded(args.gcov, args.multirtc_run, args.rslc)
    json.dump(res, open(args.out, "w"), indent=1)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
