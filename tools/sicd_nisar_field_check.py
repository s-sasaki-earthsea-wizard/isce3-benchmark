#!/usr/bin/env python3
"""Cross-check a SICD XML header against the NISAR RSLC fields isce3 requires.

This is the cheap kill-point for Stage U0 (bench#54): it answers "is there
enough in a SICD header to build an RSLC that isce3 will open?" from a 30 KB
XML dump, without downloading or converting a single pixel.

The requirement list is not the NISAR product spec -- it is what the isce3
readers actually dereference, which is a strictly smaller and more honest set:

  cxx/isce3/product/Serialization.h        loadFromH5(.., Swath&, freq)
                                           loadFromH5(.., Metadata&, level)
  cxx/isce3/product/RadarGridProduct.cpp   identification/lookDirection
  cxx/isce3/core/Serialization.h           orbit{time,position,velocity},
                                           attitude{time,quaternions}
  python/packages/nisar/products/readers/Base/Identification.py

Anything opened with `openGroup` / `loadFromH5` without an `exists` guard is
REQUIRED: a missing dataset raises, so the product will not even open.

Each field is reported with a provenance class:

  DIRECT    copied from one SICD element
  DERIVED   computed from SICD elements, formula shown
  SYNTHETIC not described by SICD; a defensible value is invented
  MISSING   no source and no defensible default

Usage:
    tools/sicd_nisar_field_check.py <sicd.xml> [<sicd.xml> ...]
    tools/sicd_nisar_field_check.py --markdown <sicd.xml>
"""

from __future__ import annotations

import argparse
import datetime
import sys
import xml.etree.ElementTree as ET

import numpy as np

SPEED_OF_LIGHT = 299792458.0

# Provenance classes, ordered worst-last so a summary can sort on them.
DIRECT, DERIVED, SYNTHETIC, MISSING = "DIRECT", "DERIVED", "SYNTHETIC", "MISSING"


class Sicd:
    """Thin namespace-agnostic accessor over a SICD XML tree."""

    def __init__(self, path: str):
        self.path = path
        self.root = ET.parse(path).getroot()
        self.ns = self.root.tag.split("}")[0].strip("{") if "}" in self.root.tag else ""

    def _q(self, path: str) -> str:
        if not self.ns:
            return path
        return "/".join(f"{{{self.ns}}}{p}" for p in path.split("/"))

    def find(self, path: str):
        return self.root.find(self._q(path))

    def findall(self, path: str):
        return self.root.findall(self._q(path))

    def text(self, path: str, default=None):
        e = self.find(path)
        return default if e is None or e.text is None else e.text.strip()

    def num(self, path: str, default=None):
        t = self.text(path)
        return default if t is None else float(t)

    def int_(self, path: str, default=None):
        t = self.text(path)
        return default if t is None else int(t)

    def poly1d(self, path: str) -> np.ndarray:
        """1-D SICD polynomial -> numpy coefficient array, index == exponent."""
        e = self.find(path)
        if e is None:
            return None
        order = int(e.get("order1"))
        coef = np.zeros(order + 1)
        for c in e:
            coef[int(c.get("exponent1"))] = float(c.text)
        return coef

    def poly2d(self, path: str) -> np.ndarray:
        """2-D SICD polynomial -> array[e1, e2]."""
        e = self.find(path)
        if e is None:
            return None
        o1, o2 = int(e.get("order1")), int(e.get("order2"))
        coef = np.zeros((o1 + 1, o2 + 1))
        for c in e:
            coef[int(c.get("exponent1")), int(c.get("exponent2"))] = float(c.text)
        return coef

    def xyz_poly(self, path: str) -> np.ndarray:
        """SICD XYZ polynomial -> array[3, order+1]."""
        parts = [self.poly1d(f"{path}/{axis}") for axis in ("X", "Y", "Z")]
        n = max(len(p) for p in parts)
        out = np.zeros((3, n))
        for i, p in enumerate(parts):
            out[i, : len(p)] = p
        return out


def _parse_collect_start(text: str) -> datetime.datetime:
    """SICD CollectStart carries nanoseconds; datetime only takes microseconds."""
    t = text.rstrip("Z")
    if "." in t:
        head, frac = t.split(".")
        frac = (frac + "000000")[:6]
        t = f"{head}.{frac}"
    return datetime.datetime.fromisoformat(t).replace(tzinfo=datetime.timezone.utc)


class Row:
    def __init__(self, field, status, source, value, note=""):
        self.field, self.status, self.source = field, status, source
        self.value, self.note = value, note


def analyze(sicd: Sicd) -> tuple[list[Row], dict]:
    """Return (rows, derived) for one SICD header."""
    rows: list[Row] = []
    d: dict = {}

    def add(field, status, source, value, note=""):
        rows.append(Row(field, status, source, value, note))

    # ---- gate: only the RGZERO / RMA-INCA half of SICD maps onto isce3 ------
    grid_type = sicd.text("Grid/Type")
    image_type = sicd.text("RMA/ImageType")
    algo = sicd.text("RMA/RMAlgoType")
    d["grid_type"] = grid_type
    d["image_type"] = image_type
    d["rejected"] = grid_type != "RGZERO" or image_type != "INCA"
    if d["rejected"]:
        return rows, d

    n_rg = sicd.int_("ImageData/NumRows")      # SICD Row == range
    n_az = sicd.int_("ImageData/NumCols")      # SICD Col == azimuth
    scp_row = sicd.int_("ImageData/SCPPixel/Row")
    scp_col = sicd.int_("ImageData/SCPPixel/Col")
    ss_rg = sicd.num("Grid/Row/SS")
    ss_az = sicd.num("Grid/Col/SS")
    r_ca_scp = sicd.num("RMA/INCA/R_CA_SCP")
    time_ca = sicd.poly1d("RMA/INCA/TimeCAPoly")
    collect_start = _parse_collect_start(sicd.text("Timeline/CollectStart"))

    d.update(n_rg=n_rg, n_az=n_az, ss_rg=ss_rg, ss_az=ss_az,
             collect_start=collect_start, time_ca=time_ca)

    # SICD image coordinates in metres, relative to the SCP pixel.
    ycol = (np.arange(n_az) - scp_col) * ss_az
    t_ca = np.polyval(time_ca[::-1], ycol)
    dt = float(time_ca[1]) * ss_az            # seconds per azimuth column
    d["t_ca"] = t_ca
    d["dt"] = dt
    d["az_reversed"] = dt < 0

    slant_range = r_ca_scp + (np.arange(n_rg) - scp_row) * ss_rg
    d["slant_range"] = slant_range

    # ---- identification ----------------------------------------------------
    side = sicd.text("SCPCOA/SideOfTrack")
    add("identification/lookDirection", DIRECT, "SCPCOA/SideOfTrack",
        {"L": "Left", "R": "Right"}[side[0]])

    t0 = collect_start + datetime.timedelta(seconds=float(t_ca.min()))
    t1 = collect_start + datetime.timedelta(seconds=float(t_ca.max()))
    d["t0"], d["t1"] = t0, t1
    add("identification/zeroDopplerStartTime", DERIVED,
        "Timeline/CollectStart + min(TimeCAPoly)", t0.isoformat())
    add("identification/zeroDopplerEndTime", DERIVED,
        "Timeline/CollectStart + max(TimeCAPoly)", t1.isoformat())

    arp_poly = sicd.xyz_poly("Position/ARPPoly")
    scp_time = sicd.num("SCPCOA/SCPTime")
    vel_poly = np.array([np.polyder(p[::-1]) for p in arp_poly])
    vel_scp = np.array([np.polyval(p, scp_time) for p in vel_poly])
    pos_scp = np.array([np.polyval(p[::-1], scp_time) for p in arp_poly])
    d["vel_scp"], d["pos_scp"] = vel_scp, pos_scp
    # Ascending/descending from the sign of the geodetic latitude rate, which
    # for a near-polar orbit is the sign of the ECEF z velocity.
    add("identification/orbitPassDirection", DERIVED,
        "sign(d/dt Position/ARPPoly.Z)",
        "Ascending" if vel_scp[2] > 0 else "Descending")

    add("identification/listOfFrequencies", SYNTHETIC, "single band -> ['A']", "A")
    add("identification/productType", SYNTHETIC, "converter constant", "RSLC")
    add("identification/missionId", DIRECT, "CollectionInfo/CollectorName",
        sicd.text("CollectionInfo/CollectorName"))
    add("identification/absoluteOrbitNumber", MISSING,
        "not described by SICD", 0, "isce3 reads it but InSAR does not use it")
    add("identification/diagnosticModeFlag", SYNTHETIC, "converter constant", 0)
    add("identification/boundingPolygon", DERIVED,
        "isce3.geometry.get_geo_perimeter_wkt, or GeoData/ImageCorners",
        "computed after the grid is built")

    # ---- swaths ------------------------------------------------------------
    # Grid/Col/UVectECF is a SICD DERIVED field: for an INCA grid it is
    # -look * v_hat (look = +1 for Left), so the azimuth axis runs backwards in
    # time for every left-looking collect, whoever the producer is.
    u_col = np.array([sicd.num(f"Grid/Col/UVectECF/{a}") for a in "XYZ"])
    d["ucol_dot_v"] = float(u_col @ vel_scp / np.linalg.norm(vel_scp))

    add("swaths/zeroDopplerTime", DERIVED,
        "Timeline/CollectStart + TimeCAPoly((col - SCPPixel.Col) * Grid/Col/SS)",
        f"{n_az} samples, {t_ca.min():.6f} .. {t_ca.max():.6f} s after CollectStart",
        "DECREASING in column order -- azimuth axis must be flipped "
        "(left-looking; SICD sets Grid/Col/UVectECF = -look * v_hat)"
        if dt < 0 else "")
    add("swaths/zeroDopplerTimeSpacing", DERIVED,
        "|TimeCAPoly[1]| * Grid/Col/SS", abs(dt),
        f"grid PRF {1.0 / abs(dt):.4f} Hz")
    add("swaths/frequencyA/slantRange", DERIVED,
        "RMA/INCA/R_CA_SCP + (row - SCPPixel.Row) * Grid/Row/SS",
        f"{n_rg} samples, {slant_range[0]:.3f} .. {slant_range[-1]:.3f} m")
    add("swaths/frequencyA/slantRangeSpacing", DIRECT, "Grid/Row/SS", ss_rg)

    f_min = sicd.num("RadarCollection/TxFrequency/Min")
    f_max = sicd.num("RadarCollection/TxFrequency/Max")
    fp_min = sicd.num("ImageFormation/TxFrequencyProc/MinProc")
    fp_max = sicd.num("ImageFormation/TxFrequencyProc/MaxProc")
    add("swaths/frequencyA/acquiredCenterFrequency", DERIVED,
        "mean(RadarCollection/TxFrequency Min,Max)", 0.5 * (f_min + f_max))
    add("swaths/frequencyA/processedCenterFrequency", DERIVED,
        "mean(ImageFormation/TxFrequencyProc Min,MaxProc)", 0.5 * (fp_min + fp_max),
        f"cross-check RMA/INCA/FreqZero = {sicd.num('RMA/INCA/FreqZero'):.1f}")
    add("swaths/frequencyA/acquiredRangeBandwidth", DERIVED,
        "TxFrequency.Max - TxFrequency.Min", f_max - f_min)
    add("swaths/frequencyA/processedRangeBandwidth", DERIVED,
        "TxFrequencyProc.MaxProc - MinProc", fp_max - fp_min,
        f"cross-check Grid/Row/ImpRespBW * c/2 = "
        f"{sicd.num('Grid/Row/ImpRespBW') * SPEED_OF_LIGHT / 2:.1f}")

    # Acquisition PRF from the IPP sets: each set is a linear IPP-vs-time poly,
    # so the slope is the pulse rate over that interval.
    ipp_sets = sicd.findall("Timeline/IPP/Set")
    prfs = []
    for s in ipp_sets:
        e = s.find(sicd._q("IPPPoly"))
        coef = {int(c.get("exponent1")): float(c.text) for c in e}
        prfs.append(coef.get(1, np.nan))
    prf_nom = float(np.mean(prfs)) if prfs else None
    d["prf_nom"] = prf_nom
    if prf_nom is not None:
        add("swaths/frequencyA/nominalAcquisitionPRF", DERIVED,
            "mean slope of Timeline/IPP/Set/IPPPoly", prf_nom,
            f"{len(prfs)} IPP sets, spread "
            f"{np.ptp(prfs):.3f} Hz; image grid is decimated to "
            f"{1.0 / abs(dt):.1f} Hz")
    else:
        add("swaths/frequencyA/nominalAcquisitionPRF", MISSING,
            "Timeline/IPP absent", None)

    inc = np.radians(sicd.num("SCPCOA/IncidenceAng"))
    gr_spacing = ss_rg / np.sin(inc)
    area_line = sicd.num("RadarCollection/Area/Plane/XDir/LineSpacing")
    add("swaths/frequencyA/sceneCenterGroundRangeSpacing", DERIVED,
        "Grid/Row/SS / sin(SCPCOA/IncidenceAng)", gr_spacing,
        "" if area_line is None else
        f"cross-check Area/Plane/XDir/LineSpacing = {area_line:.6f} "
        f"(delta {abs(gr_spacing - area_line):.2e} m)")

    area_samp = sicd.num("RadarCollection/Area/Plane/YDir/SampleSpacing")
    add("swaths/frequencyA/sceneCenterAlongTrackSpacing", DIRECT, "Grid/Col/SS",
        ss_az,
        "" if area_samp is None else
        f"cross-check Area/Plane/YDir/SampleSpacing = {area_samp:.6f}")

    az_bw = sicd.num("Grid/Col/ImpRespBW") / abs(float(time_ca[1]))
    add("swaths/frequencyA/processedAzimuthBandwidth", DERIVED,
        "Grid/Col/ImpRespBW / |TimeCAPoly[1]|", az_bw,
        f"azimuth oversampling {1.0 / abs(dt) / az_bw:.4f}")

    add("swaths/frequencyA/listOfPolarizations", DIRECT,
        "ImageFormation/TxRcvPolarizationProc",
        sicd.text("ImageFormation/TxRcvPolarizationProc").replace(":", ""))
    add("swaths/frequencyA/numberOfSubSwaths", SYNTHETIC, "stripmap -> 1", 1)
    add("swaths/frequencyA/validSamplesSubSwath1", DERIVED,
        "ImageData/ValidData polygon, or full extent", f"[0, {n_rg}] x {n_az} lines")
    add("swaths/frequencyA/<pol> image", DERIVED,
        f"NITF image segment, {sicd.text('ImageData/PixelType')}",
        f"{n_rg} x {n_az} -> transpose to {n_az} x {n_rg} complex64",
        "SICD stores range-major; NISAR wants azimuth-major")

    # ---- metadata/orbit ----------------------------------------------------
    add("metadata/orbit/{time,position,velocity}", DERIVED,
        "Position/ARPPoly sampled; velocity = d/dt ARPPoly",
        f"ARPPoly order {arp_poly.shape[1] - 1}, "
        f"|v(SCP)| = {np.linalg.norm(vel_scp):.3f} m/s")

    # ---- metadata/attitude -------------------------------------------------
    # isce3 requires the group to exist. SICD has no quaternions, but the
    # optional Antenna block carries the antenna frame axes as ECEF unit
    # vector polynomials, which is a rotation we can convert.
    xax = sicd.find("Antenna/Tx/XAxisPoly")
    if xax is not None:
        ax = sicd.xyz_poly("Antenna/Tx/XAxisPoly")
        ay = sicd.xyz_poly("Antenna/Tx/YAxisPoly")
        ts = np.linspace(t_ca.min(), t_ca.max(), 32)
        errs = []
        for t in ts:
            x = np.array([np.polyval(p[::-1], t) for p in ax])
            y = np.array([np.polyval(p[::-1], t) for p in ay])
            errs.append((abs(np.linalg.norm(x) - 1),
                         abs(np.linalg.norm(y) - 1),
                         abs(float(np.dot(x, y)))))
        errs = np.array(errs)
        d["antenna_frame_err"] = errs.max(axis=0)
        add("metadata/attitude/{time,quaternions}", DERIVED,
            "Antenna/Tx/{XAxisPoly,YAxisPoly}, Z = X x Y -> quaternion",
            f"max |‖X‖-1| {errs[:, 0].max():.2e}, "
            f"max |‖Y‖-1| {errs[:, 1].max():.2e}, "
            f"max |X·Y| {errs[:, 2].max():.2e}",
            "Antenna is an OPTIONAL SICD block; fall back to a nadir/TCN "
            "frame for producers that omit it")
    else:
        add("metadata/attitude/{time,quaternions}", SYNTHETIC,
            "Antenna block absent; build a TCN/nadir frame from the orbit",
            "isce3 InSAR geometry does not read attitude")

    # ---- metadata/processingInformation/parameters -------------------------
    dop_centroid = sicd.poly2d("RMA/INCA/DopCentroidPoly")
    dop_coa = sicd.text("RMA/INCA/DopCentroidCOA")
    cone = sicd.num("SCPCOA/DopplerConeAng")
    lam = SPEED_OF_LIGHT / (0.5 * (fp_min + fp_max))
    # Doppler implied by the grid geometry at the SCP.
    f_dop_geom = 2.0 * np.linalg.norm(vel_scp) * np.cos(np.radians(cone)) / lam
    d["f_dop_geom"] = f_dop_geom
    d["dop_centroid_00"] = float(dop_centroid[0, 0]) if dop_centroid is not None else None
    add("metadata/processingInformation/parameters/frequencyA/dopplerCentroid",
        DERIVED, "identically zero for an RGZERO grid", 0.0,
        f"geometric Doppler at SCP from DopplerConeAng = {f_dop_geom:+.4e} Hz; "
        f"RMA/INCA/DopCentroidPoly[0,0] = {d['dop_centroid_00']:+.4f} Hz "
        f"(DopCentroidCOA={dop_coa}) -- processing Doppler, NOT the grid")

    time_coa = sicd.poly2d("Grid/TimeCOAPoly")
    coa_minus_ca = float(time_coa[0, 0]) - float(time_ca[0])
    d["coa_minus_ca"] = coa_minus_ca
    add("metadata/processingInformation/parameters/frequencyA/azimuthFMRate",
        DERIVED, "RMA/INCA/DRateSFPoly (optional in isce3)",
        f"DRateSF = {sicd.num('RMA/INCA/DRateSFPoly/Coef'):.6f}"
        if sicd.find("RMA/INCA/DRateSFPoly") is not None else "absent",
        "isce3 guards this read with exists(); can be omitted")

    add("metadata/processingInformation/parameters/{zeroDopplerTime,slantRange}",
        DERIVED, "LUT coordinate vectors, any grid spanning the scene",
        "converter choice")

    add("metadata/calibrationInformation/...", SYNTHETIC,
        "Radiometric/NoiseLevel/NoisePoly -> noiseEquivalentBackscatter",
        "not required to open the product",
        "needed only by the noise-correction steps")

    add("swaths/frequencyA/chirpDuration", DIRECT,
        "RadarCollection/Waveform/WFParameters/TxPulseLength",
        sicd.num("RadarCollection/Waveform/WFParameters/TxPulseLength"),
        "not read by the isce3 Swath loader, but present anyway")
    add("swaths/frequencyA/chirpSlope", DIRECT,
        "RadarCollection/Waveform/WFParameters/TxFMRate",
        sicd.num("RadarCollection/Waveform/WFParameters/TxFMRate"))

    return rows, d


def report(sicd: Sicd, rows: list[Row], d: dict, markdown: bool) -> None:
    name = sicd.path.split("/")[-1]
    print(f"\n{'=' * 100}\n{name}\n{'=' * 100}")
    print(f"SICD version  : {sicd.ns}")
    print(f"Grid/Type     : {d['grid_type']}   RMA/ImageType: {d['image_type']}"
          f"   RMAlgoType: {sicd.text('RMA/RMAlgoType')}")
    if d["rejected"]:
        print("\nREJECTED: this converter handles Grid/Type=RGZERO with "
              "RMA/ImageType=INCA only.")
        return
    print(f"ImageFormAlgo : {sicd.text('ImageFormation/ImageFormAlgo')}"
          f"   Processing/Type: {sicd.text('ImageFormation/Processing/Type')}")
    print(f"Image         : {d['n_rg']} range x {d['n_az']} azimuth "
          f"= {d['n_rg'] * d['n_az'] / 1e6:.1f} Mpx")

    # Checks that decide whether "RGZERO" can be taken at face value.
    dt = d["dt"]
    print("\nconsistency checks")
    print(f"  COA - CA at SCP          : {d['coa_minus_ca']:+.3e} s "
          f"({abs(d['coa_minus_ca']) / abs(dt):.2e} azimuth samples) "
          "-- grid is focused at closest approach")
    print(f"  DopplerConeAng at SCP    : {sicd.num('SCPCOA/DopplerConeAng'):.9f} deg "
          f"-> geometric Doppler {d['f_dop_geom']:+.4e} Hz")
    print(f"  DopCentroidPoly[0,0]     : {d['dop_centroid_00']:+.4f} Hz "
          f"= {100 * abs(d['dop_centroid_00']) * abs(float(d['time_ca'][1])) / sicd.num('Grid/Col/ImpRespBW'):.3f} % "
          "of the processed azimuth bandwidth")
    print(f"  effective grid velocity  : {d['ss_az'] / abs(dt):.3f} m/s "
          f"(platform |v| = {np.linalg.norm(d['vel_scp']):.3f} m/s, "
          f"altitude {np.linalg.norm(d['pos_scp']) / 1e3:.1f} km geocentric)")
    print(f"  Grid Row/Col Sgn         : {sicd.text('Grid/Row/Sgn')} / "
          f"{sicd.text('Grid/Col/Sgn')}  (FFT sign convention)")
    # SICD derives Grid/Col/UVectECF = -look * v_hat for an INCA grid (look = +1
    # for Left), so the azimuth axis runs backwards in time for every
    # left-looking collect. Branch on this sign; never hard-code the flip.
    print(f"  uCol . v / |v|           : {d['ucol_dot_v']:+.6f} "
          f"(side {sicd.text('SCPCOA/SideOfTrack')}) -> azimuth axis "
          f"{'REVERSED' if d['az_reversed'] else 'forward'} in time")
    if "antenna_frame_err" in d:
        e = d["antenna_frame_err"]
        print(f"  antenna frame orthonorm. : |‖X‖-1| {e[0]:.2e}, "
              f"|‖Y‖-1| {e[1]:.2e}, |X·Y| {e[2]:.2e}")

    if markdown:
        print("\n| NISAR RSLC field | status | SICD source | value |")
        print("|---|---|---|---|")
        for r in rows:
            v = f"{r.value}" + (f" — {r.note}" if r.note else "")
            print(f"| `{r.field}` | {r.status} | `{r.source}` | {v} |")
        return

    width = max(len(r.field) for r in rows)
    print()
    for r in rows:
        val = r.value
        if isinstance(val, float):
            val = f"{val:.10g}"
        print(f"  {r.status:<9} {r.field:<{width}}  = {val}")
        print(f"  {'':<9} {'':<{width}}    <- {r.source}")
        if r.note:
            print(f"  {'':<9} {'':<{width}}    !  {r.note}")

    counts = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("\n  " + "  ".join(f"{k}={v}" for k, v in counts.items()))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("xml", nargs="+", help="SICD XML header(s) from dump_sicd_xml.py")
    p.add_argument("--markdown", action="store_true", help="emit a markdown table")
    args = p.parse_args(argv)

    worst = 0
    for path in args.xml:
        sicd = Sicd(path)
        rows, d = analyze(sicd)
        report(sicd, rows, d, args.markdown)
        if d["rejected"] or any(r.status == MISSING for r in rows):
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
