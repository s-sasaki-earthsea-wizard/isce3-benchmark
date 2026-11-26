#!/usr/bin/env python3
"""Crop a NISAR-format RSLC HDF5 to a radar-grid window around a geographic point.

Why: a full ALOS-2 Ultra-fine scene (21184 x 37914, ~800 Mpx) is ~11x a
NISAR frame, which the validated GUNW chain processes at ~33 GB RSS and
~150 GB scratch per pair. All C(12,2) pairs of the Kujukuri stack only fit
this machine if every scene is cut to a NISAR-frame-sized pixel budget.

A NISAR 40+5 MHz frame is in the same position: frequency A alone is
31920 x 52943 (~1.7 Gpx), twice the width of a 20 MHz frame.

The window is defined once in geographic terms (a centre point) and turned
into per-scene line/sample indices with zero-Doppler geo2rdr, so scenes whose
near-range start differs (the stack has 21120/21184/21248-sample widths)
still crop to the same ground patch. Coregistration absorbs the residual
sub-pixel differences.

Every frequency of the product is cropped to the same lines and slant-range
span, so a dual-band RSLC (NISAR 40+5 MHz, whose B band the ionosphere
correction needs) stays consistent: the bands share ``swaths/zeroDopplerTime``,
and B starts at A's first slant range with 8x its spacing. The window of
``--freq`` sets the span; it is snapped to the coarsest band's samples so
that every band again starts at one slant range. A product whose bands do
not share a start range and an integer spacing ratio is refused.

What is sliced: the image arrays, ``inputDataExceptionMask``,
``swaths/zeroDopplerTime``, ``swaths/frequency*/slantRange`` and
``validSamplesSubSwath*`` of every frequency, plus the identification
start/end times and the bounding polygon (recomputed with
``get_geo_perimeter_wkt``). Calibration and Doppler LUTs keep their own
coordinate vectors and are copied untouched: a crop is a subset of the grid
they already cover. Every other dataset/attribute is copied as is.

Usage::

    python tools/crop_rslc.py in.h5 out.h5 \
        --center-lon 140.36 --center-lat 35.44 \
        --half-lines 4500 --half-samples 4000 [--dem dem.tif] [--freq A]

Prints the chosen window as JSON on stdout (one line) for provenance;
``frequencies`` holds the sample window of each band.
"""

import argparse
import json
import math
import sys

import h5py
import numpy as np

# pyre's journal parses sys.argv on ``import isce3``; stash our flags first.
_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
from nisar.products.readers import SLC  # noqa: E402
sys.argv = _ARGV

SWATHS = "science/LSAR/RSLC/swaths"
IDENT = "science/LSAR/identification"


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--center-lon", type=float, required=True)
    ap.add_argument("--center-lat", type=float, required=True)
    ap.add_argument("--center-height", type=float, default=0.0,
                    help="ellipsoidal height of the centre point [m]")
    ap.add_argument("--half-lines", type=int, required=True)
    ap.add_argument("--half-samples", type=int, required=True)
    ap.add_argument("--freq", default="A",
                    help="frequency whose grid the window is computed on")
    ap.add_argument("--dem", default=None,
                    help="DEM for the bounding polygon (default: h=0 ellipsoid)")
    ap.add_argument("--dry-run", action="store_true",
                    help="only print the window, write nothing")
    return ap.parse_args()


def window_for(slc, freq, lon, lat, height, half_lines, half_samples):
    """Line/sample window of the scene around a geographic point.

    Returns:
        dict with line0/line1/sample0/sample1 (half-open), the centre's
        fractional (line, sample), and whether clamping at the grid edge
        moved the window.
    """
    grid = slc.getRadarGrid(freq)
    orbit = slc.getOrbit()
    ellipsoid = isce3.core.Ellipsoid()
    doppler = isce3.core.LUT2d()          # RSLC grids are zero-Doppler
    llh = np.array([np.deg2rad(lon), np.deg2rad(lat), height])
    aztime, srange = isce3.geometry.geo2rdr(
        llh, ellipsoid, orbit, doppler, grid.wavelength, grid.lookside,
        threshold=1e-8, maxiter=50, delta_range=1e-8)
    line_c = (aztime - grid.sensing_start) * grid.prf
    samp_c = (srange - grid.starting_range) / grid.range_pixel_spacing

    line0 = int(round(line_c)) - half_lines
    samp0 = int(round(samp_c)) - half_samples
    line1 = line0 + 2 * half_lines
    samp1 = samp0 + 2 * half_samples
    clamped = False
    if line0 < 0:
        line0, line1, clamped = 0, min(2 * half_lines, grid.length), True
    if samp0 < 0:
        samp0, samp1, clamped = 0, min(2 * half_samples, grid.width), True
    if line1 > grid.length:
        line1, line0, clamped = grid.length, max(0, grid.length - 2 * half_lines), True
    if samp1 > grid.width:
        samp1, samp0, clamped = grid.width, max(0, grid.width - 2 * half_samples), True
    return dict(line0=line0, line1=line1, sample0=samp0, sample1=samp1,
                center_line=float(line_c), center_sample=float(samp_c),
                clamped=clamped, full_length=grid.length, full_width=grid.width)


def frequency_letters(fin):
    """Frequencies present under ``swaths/``, e.g. ['A', 'B']."""
    return sorted(k[len("frequency"):] for k in fin[SWATHS]
                  if k.startswith("frequency"))


def frequency_windows(fin, freq, win):
    """Sample windows of every frequency over the slant-range span of ``win``.

    Args:
        fin: open source RSLC.
        freq: frequency letter whose samples ``win`` counts.
        win: window from window_for (line0/line1/sample0/sample1).

    Returns:
        dict letter -> dict(sample0, sample1, full_width), half-open sample
        indices that start every band at one slant range.

    Raises:
        ValueError: if a band does not start at the slant range of the others,
            its spacing is not an integer multiple of the finest band's, or
            its images do not have one line per ``swaths/zeroDopplerTime``.
    """
    n_lines = fin[f"{SWATHS}/zeroDopplerTime"].shape[0]
    bands = {}
    for f in frequency_letters(fin):
        g = fin[f"{SWATHS}/frequency{f}"]
        sr = g["slantRange"]
        bands[f] = dict(r0=float(sr[0]), dr=float(g["slantRangeSpacing"][()]),
                        width=sr.shape[0])
        for name, d in g.items():
            if (isinstance(d, h5py.Dataset) and d.ndim == 2
                    and d.shape[1] == sr.shape[0] and d.shape[0] != n_lines):
                raise ValueError(f"frequency{f}/{name} has {d.shape[0]} lines, "
                                 f"zeroDopplerTime has {n_lines}")
    if freq not in bands:
        raise ValueError(f"no frequency{freq} in the product: {sorted(bands)}")
    dr_min = min(b["dr"] for b in bands.values())
    for f, b in bands.items():
        n = b["dr"] / dr_min
        if abs(n - round(n)) > 1e-6:
            raise ValueError(f"frequency{f} spacing {b['dr']} m is not an integer "
                             f"multiple of {dr_min} m")
        if abs(b["r0"] - bands[freq]["r0"]) > 1e-3 * dr_min:
            raise ValueError(f"frequency{f} starts at {b['r0']} m, frequency{freq} "
                             f"at {bands[freq]['r0']} m")
        b["n"] = round(n)
    # In units of the finest band's samples, every band's edges lie on
    # multiples of its own ratio; the span keeps to multiples of all of them.
    step = math.lcm(*(b["n"] for b in bands.values()))
    n_win = bands[freq]["n"]
    end_max = min(b["width"] * b["n"] for b in bands.values())
    a = win["sample0"] * n_win // step * step
    e = min(win["sample1"] * n_win, end_max) // step * step
    if e <= a:
        raise ValueError(f"window {win['sample0']}:{win['sample1']} of frequency"
                         f"{freq} holds no sample of the coarsest band")
    return {f: dict(sample0=a // b["n"], sample1=e // b["n"], full_width=b["width"])
            for f, b in bands.items()}


def copy_attrs(src, dst):
    for k, v in src.attrs.items():
        dst.attrs[k] = v


def band_of(name, letters):
    """Frequency letter of a dataset under ``swaths/frequencyX/``, else None."""
    prefix = f"{SWATHS}/frequency"
    if not name.startswith(prefix):
        return None
    letter, _, tail = name[len(prefix):].partition("/")
    return letter if tail and letter in letters else None


def crop_copy(fin, fout, win):
    """Recursively copy fin -> fout, slicing the radar-grid datasets.

    ``win`` carries the lines (line0/line1, full_length) and, under
    ``frequencies``, the sample window of every band (frequency_windows).
    """
    l0, l1 = win["line0"], win["line1"]
    bands = win["frequencies"]

    def visit(name, obj):
        if isinstance(obj, h5py.Group):
            g = fout.require_group(name)
            copy_attrs(obj, g)
            return
        d = obj
        kw = dict(dtype=d.dtype, compression=d.compression,
                  compression_opts=d.compression_opts, shuffle=d.shuffle)
        f = band_of(name, bands)
        if f is not None:
            s0, s1 = bands[f]["sample0"], bands[f]["sample1"]
            freq_grp = f"{SWATHS}/frequency{f}"
        if name == f"{SWATHS}/zeroDopplerTime":
            data = d[l0:l1]
            kw["chunks"] = None
        elif f is not None and name == f"{freq_grp}/slantRange":
            data = d[s0:s1]
            kw["chunks"] = None
        elif f is not None and name.startswith(f"{freq_grp}/validSamplesSubSwath"):
            sub = d[l0:l1]
            # samples are re-indexed to the crop; clip to the new width.
            # NISAR stores them as uint32, so subtract in int64: an unsigned
            # difference wraps and the clip then puts the start at the far edge.
            sub = np.clip(sub.astype(np.int64) - s0, 0, s1 - s0)
            data = sub.astype(d.dtype)
            kw["chunks"] = None
        elif (f is not None and d.ndim == 2
              and d.shape == (win["full_length"], bands[f]["full_width"])):
            # image array (HH, HV, ..., inputDataExceptionMask): stream in
            # line blocks
            out = fout.create_dataset(name, shape=(l1 - l0, s1 - s0),
                                      chunks=(min(512, l1 - l0), min(512, s1 - s0)),
                                      **kw)
            copy_attrs(d, out)
            step = 2048
            for a in range(l0, l1, step):
                b = min(a + step, l1)
                out[a - l0:b - l0, :] = d[a:b, s0:s1]
            return
        else:
            data = d[()]
            if d.chunks is not None and d.ndim >= 1:
                kw["chunks"] = d.chunks if all(
                    c <= s for c, s in zip(d.chunks, d.shape)) else None
            else:
                kw["chunks"] = None
            if d.ndim == 0:
                kw = dict(dtype=d.dtype)
        if d.ndim == 0:
            out = fout.create_dataset(name, data=data, dtype=d.dtype)
        else:
            out = fout.create_dataset(name, data=data, **kw)
        copy_attrs(d, out)

    fin.visititems(visit)


def update_identification(fout, freq, dem_path):
    """Rewrite start/end times and the bounding polygon from the new grid."""
    zt = fout[f"{SWATHS}/zeroDopplerTime"]
    units = zt.attrs["units"]
    units = units.decode() if isinstance(units, bytes) else units
    epoch = isce3.core.DateTime(units.replace("seconds since ", "").strip())
    t0 = epoch + isce3.core.TimeDelta(float(zt[0]))
    t1 = epoch + isce3.core.TimeDelta(float(zt[-1]))
    for key, t in (("zeroDopplerStartTime", t0), ("zeroDopplerEndTime", t1)):
        ds = fout[f"{IDENT}/{key}"]
        attrs = dict(ds.attrs)
        del fout[f"{IDENT}/{key}"]
        nd = fout.create_dataset(f"{IDENT}/{key}", data=np.bytes_(t.isoformat()[:26]))
        for k, v in attrs.items():
            nd.attrs[k] = v
    fout.flush()

    slc = SLC(hdf5file=fout.filename)
    grid = slc.getRadarGrid(freq)
    orbit = slc.getOrbit()
    if dem_path:
        dem = isce3.geometry.DEMInterpolator(isce3.io.Raster(dem_path))
    else:
        dem = isce3.geometry.DEMInterpolator(0.0)
    # v0.25.16 signature: (grid, orbit, doppler, dem, points_per_edge, threshold)
    wkt = isce3.geometry.get_geo_perimeter_wkt(grid, orbit,
                                               doppler=isce3.core.LUT2d(),
                                               dem=dem, points_per_edge=11,
                                               threshold=1e-8)
    ds = fout[f"{IDENT}/boundingPolygon"]
    attrs = dict(ds.attrs)
    del fout[f"{IDENT}/boundingPolygon"]
    nd = fout.create_dataset(f"{IDENT}/boundingPolygon", data=np.bytes_(wkt))
    for k, v in attrs.items():
        nd.attrs[k] = v
    return wkt


def main():
    args = parse_args()
    slc = SLC(hdf5file=args.src)
    win = window_for(slc, args.freq, args.center_lon, args.center_lat,
                     args.center_height, args.half_lines, args.half_samples)
    with h5py.File(args.src, "r") as fin:
        bands = frequency_windows(fin, args.freq, win)
    win.update(sample0=bands[args.freq]["sample0"],
               sample1=bands[args.freq]["sample1"], frequencies=bands)
    win.update(src=args.src, dst=args.dst, freq=args.freq,
               center_lon=args.center_lon, center_lat=args.center_lat)
    if args.dry_run:
        print(json.dumps(win))
        return
    with h5py.File(args.src, "r") as fin, h5py.File(args.dst, "w") as fout:
        copy_attrs(fin, fout)
        crop_copy(fin, fout, win)
    # reopen r+ so the SLC reader can see a consistent file for the polygon
    with h5py.File(args.dst, "r+") as fout:
        wkt = update_identification(fout, args.freq, args.dem)
    win["boundingPolygon"] = wkt[:80] + "..."
    print(json.dumps(win))


if __name__ == "__main__":
    main()
