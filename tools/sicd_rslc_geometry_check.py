#!/usr/bin/env python3
"""Geometry checks for an RSLC produced by ``tools/sicd_to_nisar_rslc.py`` (bench#54, Acceptance 2).

Two independent questions, answered by three sub-commands that share one
deterministic set of image points (an n x n grid over the SICD (row, col)
space plus the SCP pixel, each on two constant-HAE surfaces):

``isce3``  (runs inside the dev container)
    Self-consistency of the converted product: ``rdr2geo_bracket`` from every
    point to a constant-HAE surface, then ``geo2rdr_bracket`` back, with the
    residual in lines / samples. Also the absolute anchor the header gives for
    free: the SCP pixel must land on ``GeoData/SCP/ECF`` at the SCP HAE, and
    ``GeoData/SCP/ECF`` must map back to the SCP pixel. This proves that the
    orbit, time axis, range axis, look side and azimuth flip are mutually
    consistent -- not that the mapping is right.

``sarkit`` (runs on the host, ``venv-sarkit`` -- sarkit is NGA's reference
    implementation of the SICD projection equations)
    The same (row, col) points projected to the same HAE surfaces from the
    original SICD XML with ``sarkit.sicd.image_to_constant_hae_surface``.
    No isce3 involved.

``compare``
    Joins the two on (row, col, hae) and reports the 3-D separation per
    point, in metres and in pixels. This is the check that actually tests the
    mapping (two implementations, one header).

Usage::

    # container
    python tools/sicd_rslc_geometry_check.py isce3  rslc.h5 --out isce3.json
    # host
    venv-sarkit/bin/python tools/sicd_rslc_geometry_check.py sarkit rslc.h5 --out sarkit.json
    # anywhere
    python tools/sicd_rslc_geometry_check.py compare isce3.json sarkit.json [--tol-m 0.01]

The SICD XML is read from the RSLC itself
(``metadata/processingInformation/inputs/sicdXml``), so both halves see the
same header bytes.
"""

from __future__ import annotations

import argparse
import io
import json
import sys

import h5py
import numpy as np

INPUTS = "science/LSAR/RSLC/metadata/processingInformation/inputs"


# --------------------------------------------------------------------------
# shared point set
# --------------------------------------------------------------------------
def _xml_text_from_h5(path: str) -> str:
    with h5py.File(path, "r") as fid:
        return fid[f"{INPUTS}/sicdXml"][()].decode("utf-8")


def _header_basics(xml_text: str) -> dict:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""

    def q(p):
        return "/".join(f"{{{ns}}}{x}" for x in p.split("/")) if ns else p

    def num(p):
        return float(root.find(q(p)).text)

    return {
        "n_rg": int(num("ImageData/NumRows")), "n_az": int(num("ImageData/NumCols")),
        "scp_row": int(num("ImageData/SCPPixel/Row")), "scp_col": int(num("ImageData/SCPPixel/Col")),
        "scp_ecf": [num(f"GeoData/SCP/ECF/{a}") for a in "XYZ"],
        "scp_llh": [num("GeoData/SCP/LLH/Lon"), num("GeoData/SCP/LLH/Lat"), num("GeoData/SCP/LLH/HAE")],
        "corners": {e.get("index"): (float(e.find(q("Lat")).text), float(e.find(q("Lon")).text))
                    for e in root.findall(q("GeoData/ImageCorners/ICP"))},
        "ss_rg": num("Grid/Row/SS"), "ss_az": num("Grid/Col/SS"),
    }


def point_set(h: dict, n: int, extra_hae: float) -> list[dict]:
    rows = np.linspace(0, h["n_rg"] - 1, n).round().astype(int)
    cols = np.linspace(0, h["n_az"] - 1, n).round().astype(int)
    pts = []
    for hae in (h["scp_llh"][2], h["scp_llh"][2] + extra_hae):
        pts.append({"row": h["scp_row"], "col": h["scp_col"], "hae": float(hae), "tag": "SCP"})
        for r in rows:
            for c in cols:
                pts.append({"row": int(r), "col": int(c), "hae": float(hae), "tag": "grid"})
    return pts


# --------------------------------------------------------------------------
# isce3 half
# --------------------------------------------------------------------------
def run_isce3(args) -> int:
    _argv = sys.argv
    sys.argv = [sys.argv[0]]
    import isce3
    from nisar.products.readers import SLC
    sys.argv = _argv

    xml_text = _xml_text_from_h5(args.rslc)
    h = _header_basics(xml_text)
    with h5py.File(args.rslc, "r") as fid:
        flipped = fid[f"{INPUTS}/azimuthFlipped"][()].decode() == "True"
    slc = SLC(hdf5file=args.rslc)
    grid = slc.getRadarGrid()
    orbit = slc.getOrbit()
    dop = slc.getDopplerCentroid()
    side = grid.lookside
    wvl = grid.wavelength
    ell = isce3.core.Ellipsoid()
    assert (grid.length, grid.width) == (h["n_az"], h["n_rg"]), (grid.shape, h)

    def to_line_sample(row, col):
        line = h["n_az"] - 1 - col if flipped else col
        return line, row

    def fwd(line, sample, hae):
        t = grid.sensing_start + line * grid.az_time_interval
        r = grid.starting_range + sample * grid.range_pixel_spacing
        xyz = isce3.geometry.rdr2geo_bracket(t, r, orbit, side, 0.0, wvl,
                                             isce3.geometry.DEMInterpolator(hae))
        return np.asarray(xyz)

    def inv(xyz):
        t2, r2 = isce3.geometry.geo2rdr_bracket(np.asarray(xyz), orbit, dop, wvl, side)
        return ((t2 - grid.sensing_start) / grid.az_time_interval,
                (r2 - grid.starting_range) / grid.range_pixel_spacing)

    out = {"mode": "isce3", "rslc": args.rslc, "isce3_version": isce3.__version__,
           "azimuth_flipped": flipped, "grid": {"length": grid.length, "width": grid.width,
           "prf": grid.prf, "starting_range": grid.starting_range,
           "range_pixel_spacing": grid.range_pixel_spacing, "wavelength": wvl,
           "sensing_start": grid.sensing_start, "ref_epoch": str(grid.ref_epoch),
           "lookside": str(side)}, "points": []}

    for p in point_set(h, args.n, args.extra_hae):
        line, sample = to_line_sample(p["row"], p["col"])
        xyz = fwd(line, sample, p["hae"])
        l2, s2 = inv(xyz)
        llh = ell.xyz_to_lon_lat(xyz)
        out["points"].append({**p, "line": line, "sample": sample,
                              "xyz": xyz.tolist(),
                              "lon": float(np.degrees(llh[0])), "lat": float(np.degrees(llh[1])),
                              "h": float(llh[2]),
                              "dline": float(l2 - line), "dsample": float(s2 - sample)})

    # Absolute anchors from the header.
    line_scp, samp_scp = to_line_sample(h["scp_row"], h["scp_col"])
    xyz_scp = fwd(line_scp, samp_scp, h["scp_llh"][2])
    l_back, s_back = inv(np.asarray(h["scp_ecf"]))
    out["scp"] = {
        "expected_line_sample": [line_scp, samp_scp],
        "rdr2geo_minus_scp_ecf_m": float(np.linalg.norm(xyz_scp - np.asarray(h["scp_ecf"]))),
        "geo2rdr_of_scp_ecf_line_sample": [float(l_back), float(s_back)],
        "geo2rdr_residual_lines_samples": [float(l_back - line_scp), float(s_back - samp_scp)],
    }
    corner_px = {"1:FRFC": (0, 0), "2:FRLC": (0, h["n_az"] - 1),
                 "3:LRLC": (h["n_rg"] - 1, h["n_az"] - 1), "4:LRFC": (h["n_rg"] - 1, 0)}
    out["corners_informational"] = {}
    for key, (r, c) in corner_px.items():
        if key not in h["corners"]:
            continue
        lat, lon = h["corners"][key]
        line, sample = to_line_sample(r, c)
        xyz = fwd(line, sample, h["scp_llh"][2])
        ref = np.asarray(ell.lon_lat_to_xyz([np.radians(lon), np.radians(lat), h["scp_llh"][2]]))
        out["corners_informational"][key] = {
            "row_col": [r, c], "sicd_lat_lon": [lat, lon],
            "distance_m_at_scp_hae": float(np.linalg.norm(xyz - ref)),
            "note": "ImageCorners carry no height; distance evaluated at the SCP HAE"}

    dl = np.array([p["dline"] for p in out["points"]])
    ds = np.array([p["dsample"] for p in out["points"]])
    out["round_trip"] = {"n": len(dl), "max_abs_dline": float(np.abs(dl).max()),
                         "max_abs_dsample": float(np.abs(ds).max()),
                         "rms_dline": float(np.sqrt((dl ** 2).mean())),
                         "rms_dsample": float(np.sqrt((ds ** 2).mean()))}
    json.dump(out, open(args.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "points"}, indent=1))
    return 0


# --------------------------------------------------------------------------
# sarkit half
# --------------------------------------------------------------------------
def run_sarkit(args) -> int:
    import lxml.etree as et
    import sarkit
    import sarkit.sicd as sksicd
    import sarkit.wgs84 as wgs84

    src = args.rslc
    xml_text = (open(src, encoding="utf-8").read() if src.endswith(".xml")
                else _xml_text_from_h5(src))
    h = _header_basics(xml_text)
    tree = et.parse(io.BytesIO(xml_text.encode("utf-8")))

    pts = point_set(h, args.n, args.extra_hae)
    out = {"mode": "sarkit", "source": src, "sarkit_version": sarkit.__version__, "points": []}
    for hae in sorted({p["hae"] for p in pts}):
        sel = [p for p in pts if p["hae"] == hae]
        rowcol = np.array([[p["row"], p["col"]] for p in sel], dtype=float)
        xrow_ycol = sksicd.rowcol_to_xrowycol(tree, rowcol)
        spp, dhae, ok = sksicd.image_to_constant_hae_surface(tree, xrow_ycol, hae)
        for p, xyz, dh in zip(sel, spp, dhae):
            llh = wgs84.cartesian_to_geodetic(xyz)
            out["points"].append({**p, "xyz": np.asarray(xyz).tolist(),
                                  "delta_hae": float(dh), "success": bool(ok),
                                  "llh_sarkit": np.asarray(llh).tolist()})
    # Anchor: SCP ECF back to image coordinates must give xrow = ycol = 0.
    img, dgp, ok = sksicd.scene_to_image(tree, np.asarray(h["scp_ecf"]))
    out["scp"] = {"scene_to_image_xrow_ycol_m": np.asarray(img).tolist(),
                  "scene_to_image_delta_gp": float(np.asarray(dgp).ravel()[0]), "success": bool(ok)}
    json.dump(out, open(args.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "points"}, indent=1))
    return 0


# --------------------------------------------------------------------------
def run_compare(args) -> int:
    a = json.load(open(args.isce3_json))
    b = json.load(open(args.sarkit_json))
    key = lambda p: (p["row"], p["col"], round(p["hae"], 6))  # noqa: E731
    bmap = {key(p): p for p in b["points"]}
    rows = []
    for p in a["points"]:
        q = bmap.get(key(p))
        if q is None:
            continue
        d = np.asarray(p["xyz"]) - np.asarray(q["xyz"])
        # Split into vertical (along local up at the point) and horizontal.
        up = np.asarray(p["xyz"]) / np.linalg.norm(p["xyz"])  # geocentric up is fine at cm level
        dv = float(d @ up)
        dh = float(np.sqrt(max(float(d @ d) - dv * dv, 0.0)))
        rows.append((p["tag"], p["row"], p["col"], p["hae"], float(np.linalg.norm(d)), dh, dv,
                     p["dline"], p["dsample"]))
    if not rows:
        print("no common points")
        return 2
    d3 = np.array([r[4] for r in rows])
    print(f"{'tag':4s} {'row':>6s} {'col':>6s} {'hae':>9s} {'|d| m':>10s} {'d_h m':>10s} "
          f"{'d_v m':>10s} {'dline':>10s} {'dsamp':>10s}")
    for r in rows:
        print(f"{r[0]:4s} {r[1]:6d} {r[2]:6d} {r[3]:9.2f} {r[4]:10.5f} {r[5]:10.5f} {r[6]:10.5f} "
              f"{r[7]:10.2e} {r[8]:10.2e}")
    ss = min(a["grid"]["range_pixel_spacing"], 1.0625)
    print(f"\nisce3 {a['isce3_version']} vs sarkit {b['sarkit_version']}: n={len(rows)}  "
          f"|d| max={d3.max():.5f} m  median={np.median(d3):.5f} m  "
          f"(max = {d3.max() / ss:.4f} px at {ss} m)")
    print(f"isce3 round trip: max|dline|={a['round_trip']['max_abs_dline']:.2e}  "
          f"max|dsample|={a['round_trip']['max_abs_dsample']:.2e}")
    print(f"isce3 SCP anchor: rdr2geo - SCP_ECF = {a['scp']['rdr2geo_minus_scp_ecf_m']:.5f} m; "
          f"geo2rdr(SCP_ECF) residual (lines, samples) = {a['scp']['geo2rdr_residual_lines_samples']}")
    print(f"sarkit SCP anchor: scene_to_image(SCP_ECF) xrow,ycol = {b['scp']['scene_to_image_xrow_ycol_m']} m")
    ok = d3.max() <= args.tol_m
    print("RESULT:", "PASS" if ok else "FAIL", f"(tolerance {args.tol_m} m)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("isce3", "sarkit"):
        s = sub.add_parser(name)
        s.add_argument("rslc", help="RSLC HDF5 (sarkit mode also accepts a .xml)")
        s.add_argument("--out", required=True)
        s.add_argument("--n", type=int, default=5, help="n x n grid of points")
        s.add_argument("--extra-hae", type=float, default=500.0,
                       help="second surface = SCP HAE + this [m]")
    c = sub.add_parser("compare")
    c.add_argument("isce3_json")
    c.add_argument("sarkit_json")
    c.add_argument("--tol-m", type=float, default=0.01)
    args = ap.parse_args(argv)
    return {"isce3": run_isce3, "sarkit": run_sarkit, "compare": run_compare}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
