#!/usr/bin/env python3
"""Convert an RGZERO / RMA-INCA SICD (NITF 2.1) into a NISAR-format RSLC HDF5.

Scope (bench#54, Stage U0): only the half of SICD whose image grid *is* a
zero-Doppler slant-range grid -- ``Grid/Type = RGZERO`` with
``RMA/ImageType = INCA`` (Capella stripmap / sliding-spotlight). Anything else
(``RGAZIM`` / PFA spotlight, i.e. every Umbra scene and Capella SP) is rejected
loudly, naming the grid type, because it needs new geometry code rather than a
repackaging.

The output mirrors the layout written by isce3's own
``share/nisar/examples/alos2_to_nisar_l1.py`` so that everything the isce3
readers dereference (``cxx/isce3/product/Serialization.h``,
``nisar/products/readers``) is present. See ``tools/sicd_nisar_field_check.py``
for the field-by-field provenance (DIRECT / DERIVED / SYNTHETIC / MISSING).

Conventions that are easy to get wrong -- all derived from the header, never
hard-coded:

* **Azimuth axis.** SICD sets ``Grid/Col/UVectECF = -look * v_hat`` for an INCA
  grid, so zero-Doppler time *decreases* with column index on every
  left-looking collect. NISAR needs it increasing. The flip is decided by
  ``sign(RMA/INCA/TimeCAPoly[1])`` and applied to the image, the valid-sample
  rows and the start/end times together.
* **Pixels.** SICD is range-major (rows = range, cols = azimuth); NISAR is
  azimuth-major. The transpose is done in blocks of output lines.
* **Native Doppler.** Zero by construction for an RGZERO grid (the LUT written
  under ``processingInformation/parameters`` is identically 0). The
  ``DopCentroidPoly`` in the header is the *processing* Doppler, not the grid.
* **Valid samples.** ``ImageData/ValidData`` is not trusted; the per-line valid
  range interval is derived from the data (first/last non-zero sample).
* **Reference epoch.** Midnight UTC of ``Timeline/CollectStart``; all time
  vectors are seconds since that epoch (NISAR requires an integer-second epoch).

Diagnostics written into ``metadata/processingInformation/inputs`` and printed
as one JSON line at the end: DN amplitude statistics (endianness sanity), and
the measured range / azimuth spectral centroids in cycles per pixel next to the
values the header implies (``DeltaKCOAPoly * SS``) and the value a
phase-compensated-to-grid image would show (``frac(KCtr * SS)``). These are the
single-scene, header-vs-data consistency checks behind the
"Backprojected to DEM" question in bench#54 -- necessary conditions, not a
proof of the phase convention.

Usage::

    python tools/sicd_to_nisar_rslc.py in.ntf out.h5 [--dry-run] [--xml alt.xml]
        [--block-lines 512] [--orbit-spacing 0.1] [--orbit-margin 1.0]
        [--spectrum-lines 256] [--no-compress]

``--dry-run`` parses and reports without touching the pixels or writing the
HDF5; with ``--xml`` the SICD XML is taken from a file instead of the NITF,
which is how the RGAZIM rejection is exercised without a 12 GB Umbra download.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time

import h5py
import numpy as np

# pyre's journal parses sys.argv on ``import isce3``; stash our flags first.
_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
sys.argv = _ARGV

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_sicd_xml import extract_sicd_xml, parse_nitf_header  # noqa: E402
from sicd_nisar_field_check import Sicd, _parse_collect_start  # noqa: E402

SPEED_OF_LIGHT = 299792458.0
LSAR = "science/LSAR"
IDENT = f"{LSAR}/identification"
RSLC = f"{LSAR}/RSLC"
SWATHS = f"{RSLC}/swaths"
META = f"{RSLC}/metadata"


# --------------------------------------------------------------------------
# NITF image subheader (MIL-STD-2500C table A-3), enough to validate the pixels
# --------------------------------------------------------------------------
_IMSUB_FIXED = [
    ("IM", 2), ("IID1", 10), ("IDATIM", 14), ("TGTID", 17), ("IID2", 80),
    ("ISCLAS", 1), ("ISCLSY", 2), ("ISCODE", 11), ("ISCTLH", 2), ("ISREL", 20),
    ("ISDCTP", 2), ("ISDCDT", 8), ("ISDCXM", 4), ("ISDG", 1), ("ISDGDT", 8),
    ("ISCLTX", 43), ("ISCATP", 1), ("ISCAUT", 40), ("ISCRSN", 1),
    ("ISSRDT", 8), ("ISCTLN", 15), ("ENCRYP", 1), ("ISORCE", 42),
    ("NROWS", 8), ("NCOLS", 8), ("PVTYPE", 3), ("IREP", 8), ("ICAT", 8),
    ("ABPP", 2), ("PJUST", 1), ("ICORDS", 1),
]


def parse_image_subheader(raw: bytes) -> dict:
    """Parse the NITF image subheader fields that decide the pixel layout."""
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        v = raw[pos:pos + n]
        pos += n
        return v

    d: dict = {}
    for name, width in _IMSUB_FIXED:
        d[name] = take(width).decode("latin1")
    if d["ICORDS"].strip():
        d["IGEOLO"] = take(60).decode("latin1")
    nicom = int(take(1))
    d["ICOM"] = [take(80).decode("latin1") for _ in range(nicom)]
    d["IC"] = take(2).decode("latin1")
    if d["IC"] not in ("NC", "NM"):
        d["COMRAT"] = take(4).decode("latin1")
    nbands = int(take(1))
    if nbands == 0:
        nbands = int(take(5))
    bands = []
    for _ in range(nbands):
        b = {
            "IREPBAND": take(2).decode("latin1"),
            "ISUBCAT": take(6).decode("latin1").strip(),
            "IFC": take(1).decode("latin1"),
            "IMFLT": take(3).decode("latin1"),
        }
        nluts = int(take(1))
        if nluts > 0:
            nelut = int(take(5))
            take(nelut * nluts)
        bands.append(b)
    d["bands"] = bands
    d["ISYNC"] = take(1).decode("latin1")
    d["IMODE"] = take(1).decode("latin1")
    d["NBPR"] = int(take(4))
    d["NBPC"] = int(take(4))
    d["NPPBH"] = int(take(4))
    d["NPPBV"] = int(take(4))
    d["NBPP"] = int(take(2))
    d["NROWS"] = int(d["NROWS"])
    d["NCOLS"] = int(d["NCOLS"])
    d["ABPP"] = int(d["ABPP"])
    return d


def open_pixels(path: str, n_rg: int, n_az: int, pixel_type: str):
    """Return (memmap, layout) over the single SICD image segment.

    The memmap has shape (n_rg, n_az, 2) in the file's native (big-endian)
    integer or float type; index 0/1 of the last axis are I/Q.
    """
    with open(path, "rb") as fh:
        head = fh.read(2048)
    fields = parse_nitf_header(head)
    images = fields["_segments"]["NUMI"]
    if len(images) != 1:
        raise SystemExit(
            f"REJECTED: {len(images)} image segments; this converter handles a "
            "single-segment SICD only (multi-segment = > 10 GB NITF).")
    seg = images[0]
    with open(path, "rb") as fh:
        fh.seek(seg["subheader_offset"])
        sub = parse_image_subheader(fh.read(seg["subheader_length"]))

    expected_dtype = {"RE16I_IM16I": (">i2", "SI", 16),
                      "RE32F_IM32F": (">f4", "R", 32)}
    if pixel_type not in expected_dtype:
        raise SystemExit(f"REJECTED: unsupported ImageData/PixelType {pixel_type}")
    dtype, pvtype, nbpp = expected_dtype[pixel_type]

    problems = []
    if sub["IC"] != "NC":
        problems.append(f"IC={sub['IC']} (compressed)")
    if sub["IMODE"] != "P":
        problems.append(f"IMODE={sub['IMODE']} (need pixel-interleaved P)")
    if (sub["NBPR"], sub["NBPC"]) != (1, 1):
        problems.append(f"NBPR x NBPC = {sub['NBPR']} x {sub['NBPC']} (need 1 x 1)")
    if len(sub["bands"]) != 2 or [b["ISUBCAT"] for b in sub["bands"]] != ["I", "Q"]:
        problems.append(f"bands {[b['ISUBCAT'] for b in sub['bands']]} (need I, Q)")
    if sub["PVTYPE"].strip() != pvtype or sub["NBPP"] != nbpp:
        problems.append(f"PVTYPE/NBPP {sub['PVTYPE']}/{sub['NBPP']} vs XML {pixel_type}")
    if (sub["NROWS"], sub["NCOLS"]) != (n_rg, n_az):
        problems.append(f"NROWS x NCOLS {sub['NROWS']} x {sub['NCOLS']} vs XML {n_rg} x {n_az}")
    nbytes = n_rg * n_az * 2 * (nbpp // 8)
    if seg["data_length"] != nbytes:
        problems.append(f"image segment length {seg['data_length']} != {nbytes}")
    if problems:
        raise SystemExit("REJECTED: NITF image segment layout not supported: "
                         + "; ".join(problems))

    mm = np.memmap(path, dtype=dtype, mode="r", offset=seg["data_offset"],
                   shape=(n_rg, n_az, 2))
    layout = {"data_offset": seg["data_offset"], "data_length": seg["data_length"],
              "dtype": dtype, "IMODE": sub["IMODE"], "IC": sub["IC"],
              "NBPR": sub["NBPR"], "NBPC": sub["NBPC"], "IID2": sub["IID2"].strip(),
              "ISORCE": sub["ISORCE"].strip(), "IDATIM": sub["IDATIM"]}
    return mm, layout


# --------------------------------------------------------------------------
# SICD -> radar grid
# --------------------------------------------------------------------------
def _dt_from_iso_ns(text: str) -> isce3.core.DateTime:
    """isce3 DateTime from an ISO string that may carry nanoseconds."""
    t = text.rstrip("Z")
    if "." in t:
        head, frac = t.split(".")
        frac = (frac + "000000000")[:9]
        return isce3.core.DateTime(head) + isce3.core.TimeDelta(int(frac) * 1e-9)
    return isce3.core.DateTime(t)


def _polyval_xyz(coef: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Evaluate a (3, order+1) SICD XYZ polynomial at times t -> (len(t), 3)."""
    return np.stack([np.polyval(c[::-1], t) for c in coef], axis=1)


def _polyder_xyz(coef: np.ndarray) -> np.ndarray:
    out = np.zeros((3, max(coef.shape[1] - 1, 1)))
    for i in range(3):
        d = np.polyder(coef[i][::-1])[::-1]
        out[i, : len(d)] = d
    return out


def build_grid(sicd: Sicd) -> dict:
    """Derive everything about the radar grid from the header. Raises on RGAZIM."""
    g: dict = {}
    grid_type = sicd.text("Grid/Type")
    image_type = sicd.text("RMA/ImageType")
    image_plane = sicd.text("Grid/ImagePlane")
    algo = sicd.text("ImageFormation/ImageFormAlgo")
    if grid_type != "RGZERO" or image_type != "INCA" or image_plane != "SLANT":
        raise SystemExit(
            f"REJECTED: Grid/Type={grid_type} RMA/ImageType={image_type} "
            f"ImagePlane={image_plane} ImageFormAlgo={algo}. This converter "
            "handles RGZERO / RMA-INCA / SLANT only; a polar-format (RGAZIM/PFA) "
            "grid needs new geometry, not a repackaging.")

    time_ca = sicd.poly1d("RMA/INCA/TimeCAPoly")
    if time_ca is None or len(time_ca) != 2:
        raise SystemExit(
            f"REJECTED: RMA/INCA/TimeCAPoly order {None if time_ca is None else len(time_ca) - 1}; "
            "a NISAR grid needs a uniform zero-Doppler time axis (order 1).")

    n_rg = sicd.int_("ImageData/NumRows")
    n_az = sicd.int_("ImageData/NumCols")
    if sicd.int_("ImageData/FirstRow") or sicd.int_("ImageData/FirstCol"):
        raise SystemExit("REJECTED: ImageData/FirstRow or FirstCol != 0 (sub-image)")
    scp_row = sicd.int_("ImageData/SCPPixel/Row")
    scp_col = sicd.int_("ImageData/SCPPixel/Col")
    ss_rg = sicd.num("Grid/Row/SS")
    ss_az = sicd.num("Grid/Col/SS")
    r_ca_scp = sicd.num("RMA/INCA/R_CA_SCP")

    collect_start = _dt_from_iso_ns(sicd.text("Timeline/CollectStart"))
    epoch = isce3.core.DateTime(collect_start.year, collect_start.month,
                                collect_start.day)
    cs_offset = (collect_start - epoch).total_seconds()  # seconds since epoch

    # Seconds after CollectStart at closest approach, per SICD column.
    slope = float(time_ca[1]) * ss_az               # s per column
    t_col0 = float(time_ca[0]) - scp_col * slope   # t_ca at column 0
    flip = slope < 0
    dt = abs(slope)
    # NISAR line l <-> SICD column c; with the flip c = n_az - 1 - l.
    t_line0 = t_col0 + (n_az - 1) * slope if flip else t_col0
    zero_doppler_time = cs_offset + t_line0 + dt * np.arange(n_az)
    slant_range = r_ca_scp + (np.arange(n_rg) - scp_row) * ss_rg

    side = sicd.text("SCPCOA/SideOfTrack")[0].upper()
    lookside = {"L": "Left", "R": "Right"}[side]
    ucol = np.array([sicd.num(f"Grid/Col/UVectECF/{a}") for a in "XYZ"])

    fp_min = sicd.num("ImageFormation/TxFrequencyProc/MinProc")
    fp_max = sicd.num("ImageFormation/TxFrequencyProc/MaxProc")
    f_min = sicd.num("RadarCollection/TxFrequency/Min")
    f_max = sicd.num("RadarCollection/TxFrequency/Max")
    fc_proc = 0.5 * (fp_min + fp_max)

    ipp = []
    for s in sicd.findall("Timeline/IPP/Set"):
        e = s.find(sicd._q("IPPPoly"))
        coef = {int(c.get("exponent1")): float(c.text) for c in e}
        ipp.append(coef.get(1, np.nan))
    prf_acq = float(np.nanmean(ipp)) if ipp else 1.0 / dt

    g.update(
        n_rg=n_rg, n_az=n_az, scp_row=scp_row, scp_col=scp_col,
        ss_rg=ss_rg, ss_az=ss_az, r_ca_scp=r_ca_scp,
        collect_start=collect_start, epoch=epoch, cs_offset=cs_offset,
        collect_duration=sicd.num("Timeline/CollectDuration"),
        time_ca=time_ca, slope=slope, dt=dt, flip=flip,
        t_img=(t_line0, t_line0 + dt * (n_az - 1)),  # s after CollectStart
        zero_doppler_time=zero_doppler_time, slant_range=slant_range,
        lookside=lookside, ucol=ucol,
        fc_proc=fc_proc, fc_acq=0.5 * (f_min + f_max),
        bw_proc=fp_max - fp_min, bw_acq=f_max - f_min,
        wavelength=SPEED_OF_LIGHT / fc_proc,
        prf_acq=prf_acq, prf_grid=1.0 / dt,
        incidence_deg=sicd.num("SCPCOA/IncidenceAng"),
        az_bw=sicd.num("Grid/Col/ImpRespBW") / abs(float(time_ca[1])),
        chirp_duration=sicd.num("RadarCollection/Waveform/WFParameters/TxPulseLength"),
        chirp_slope=sicd.num("RadarCollection/Waveform/WFParameters/TxFMRate"),
        pol=sicd.text("ImageFormation/TxRcvPolarizationProc").replace(":", ""),
        scp_llh=(sicd.num("GeoData/SCP/LLH/Lon"), sicd.num("GeoData/SCP/LLH/Lat"),
                 sicd.num("GeoData/SCP/LLH/HAE")),
        scp_ecf=np.array([sicd.num(f"GeoData/SCP/ECF/{a}") for a in "XYZ"]),
        pixel_type=sicd.text("ImageData/PixelType"),
        grid_type=grid_type, image_type=image_type, algo=algo,
        processing_type=sicd.text("ImageFormation/Processing/Type"),
        processing_applied=sicd.text("ImageFormation/Processing/Applied"),
        # Spectral bookkeeping for the diagnostics.
        row_kctr=sicd.num("Grid/Row/KCtr"), col_kctr=sicd.num("Grid/Col/KCtr"),
        row_dkcoa=sicd.poly2d("Grid/Row/DeltaKCOAPoly")[0, 0],
        col_dkcoa=sicd.poly2d("Grid/Col/DeltaKCOAPoly")[0, 0],
        row_bw_cpm=sicd.num("Grid/Row/ImpRespBW"), col_bw_cpm=sicd.num("Grid/Col/ImpRespBW"),
        row_sgn=sicd.text("Grid/Row/Sgn"), col_sgn=sicd.text("Grid/Col/Sgn"),
        noise_db=(sicd.poly2d("Radiometric/NoiseLevel/NoisePoly")[0, 0]
                  if sicd.find("Radiometric/NoiseLevel/NoisePoly") is not None else None),
    )
    return g


def build_orbit(sicd: Sicd, g: dict, spacing: float, margin: float) -> isce3.core.Orbit:
    """Sample Position/ARPPoly (seconds after CollectStart) into state vectors."""
    arp = sicd.xyz_poly("Position/ARPPoly")
    vel = _polyder_xyz(arp)
    t0 = min(g["t_img"][0], 0.0) - margin
    t1 = max(g["t_img"][1], g["collect_duration"]) + margin
    n = int(np.ceil((t1 - t0) / spacing)) + 1
    ts = t0 + spacing * np.arange(n)
    pos = _polyval_xyz(arp, ts)
    vel_v = _polyval_xyz(vel, ts)
    svs = []
    for t, p, v in zip(ts, pos, vel_v):
        svs.append(isce3.core.StateVector(
            datetime=g["collect_start"] + isce3.core.TimeDelta(float(t)),
            position=p.tolist(), velocity=v.tolist()))
    orbit = isce3.core.Orbit(state_vectors=svs, date_time=g["epoch"], type="Custom")
    g["orbit_span"] = (float(ts[0]), float(ts[-1]))
    g["orbit_n"] = n
    # Sanity: speed and geocentric radius across the sampled span.
    g["orbit_speed"] = (float(np.linalg.norm(vel_v, axis=1).min()),
                        float(np.linalg.norm(vel_v, axis=1).max()))
    g["orbit_radius_km"] = (float(np.linalg.norm(pos, axis=1).min() / 1e3),
                            float(np.linalg.norm(pos, axis=1).max() / 1e3))
    return orbit


def build_attitude(sicd: Sicd, g: dict, orbit: isce3.core.Orbit,
                   spacing: float) -> tuple[isce3.core.Attitude, str]:
    """Antenna-frame -> ECEF quaternions from Antenna/Tx axes, or a TCN fallback.

    isce3.core.Attitude stores the rotation from the antenna frame to ECEF, so
    the rotation matrix has the antenna axes (in ECEF) as its columns.
    """
    t0, t1 = g["orbit_span"]
    n = int(np.ceil((t1 - t0) / spacing)) + 1
    ts = t0 + spacing * np.arange(n)
    times = [g["cs_offset"] + float(t) for t in ts]
    quats = []
    if sicd.find("Antenna/Tx/XAxisPoly") is not None:
        source = "Antenna/Tx/{XAxisPoly,YAxisPoly}, Z = X x Y"
        ax = _polyval_xyz(sicd.xyz_poly("Antenna/Tx/XAxisPoly"), ts)
        ay = _polyval_xyz(sicd.xyz_poly("Antenna/Tx/YAxisPoly"), ts)
        worst = 0.0
        for x, y in zip(ax, ay):
            x = x / np.linalg.norm(x)
            worst = max(worst, abs(float(x @ y)))
            y = y - (x @ y) * x
            y = y / np.linalg.norm(y)
            z = np.cross(x, y)
            quats.append(isce3.core.Quaternion(np.stack([x, y, z], axis=1)))
        g["attitude_max_xdoty"] = worst
    else:
        source = "TCN frame from the orbit (Antenna block absent)"
        for t in times:
            p, v = orbit.interpolate(t)
            p, v = np.asarray(p), np.asarray(v)
            tt = v / np.linalg.norm(v)
            nn = -p / np.linalg.norm(p)
            cc = np.cross(nn, tt)
            cc = cc / np.linalg.norm(cc)
            nn = np.cross(tt, cc)
            quats.append(isce3.core.Quaternion(np.stack([tt, cc, nn], axis=1)))
    att = isce3.core.Attitude(times, quats, g["epoch"])
    return att, source


# --------------------------------------------------------------------------
# HDF5 writer
# --------------------------------------------------------------------------
def _str(group: h5py.Group, name: str, value: str, desc: str | None = None):
    ds = group.create_dataset(name, data=np.bytes_(value))
    if desc:
        ds.attrs["description"] = np.bytes_(desc)
    return ds


def _num(group: h5py.Group, name: str, value, units: str | None = None,
         desc: str | None = None, dtype=None):
    ds = group.create_dataset(name, data=np.asarray(value, dtype=dtype))
    if units:
        ds.attrs["units"] = np.bytes_(units)
    if desc:
        ds.attrs["description"] = np.bytes_(desc)
    return ds


def write_skeleton(fid: h5py.File, sicd: Sicd, g: dict, orbit, att, att_source,
                   in_path: str, xml_text: str, layout: dict) -> None:
    epoch_attr = f"seconds since {g['epoch'].isoformat()}"
    fid.attrs["Conventions"] = np.bytes_("CF-1.7")

    # ---- identification ----------------------------------------------
    ident = fid.create_group(IDENT)
    _num(ident, "diagnosticModeFlag", 0, dtype=np.uint8)
    _str(ident, "isGeocoded", "False")
    ident.create_dataset("listOfFrequencies", data=np.bytes_(["A"]))
    _str(ident, "missionId", sicd.text("CollectionInfo/CollectorName"))
    _str(ident, "platformName", sicd.text("CollectionInfo/CollectorName"))
    _str(ident, "instrumentName", "SAR")
    _str(ident, "productType", "RSLC")
    _str(ident, "productVersion", "0.1.0")
    _str(ident, "productLevel", "L1")
    _str(ident, "productSpecificationVersion", "0.9.0")
    _str(ident, "processingType", "repackaging")
    _str(ident, "processingCenter",
         f"{sicd.text('ImageCreation/Site', '')} / {sicd.text('ImageCreation/Application', '')}"
         " (SICD repackaged by isce3-benchmark tools/sicd_to_nisar_rslc.py)")
    _str(ident, "processingDateTime",
         datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat())
    _str(ident, "granuleId", sicd.text("CollectionInfo/CoreName", "None"))
    _str(ident, "radarBand", _radar_band(g["fc_proc"]))
    _num(ident, "absoluteOrbitNumber", 0, dtype="u4")
    _num(ident, "trackNumber", 0, dtype=np.uint8)
    _num(ident, "frameNumber", 0, dtype=np.uint16)
    for name in ("isUrgentObservation", "isJointObservation", "isDithered", "isMixedMode"):
        _str(ident, name, "False")
    _num(ident, "hasInputDataException", 0, dtype=np.uint8,
         desc="0: no input data anomalies (NISAR_PIX #351 field, read at info level)")
    ident.create_dataset("plannedObservationId", data=np.bytes_(["0"]))
    ident.create_dataset("plannedDatatakeId", data=np.bytes_(["0"]))
    _str(ident, "lookDirection", g["lookside"])
    # Ascending/descending from the sign of the ECEF z-velocity at scene centre.
    t_mid = 0.5 * (g["zero_doppler_time"][0] + g["zero_doppler_time"][-1])
    _, v_mid = orbit.interpolate(float(t_mid))
    _str(ident, "orbitPassDirection", "Ascending" if v_mid[2] > 0 else "Descending")
    t_start = g["epoch"] + isce3.core.TimeDelta(float(g["zero_doppler_time"][0]))
    t_end = g["epoch"] + isce3.core.TimeDelta(float(g["zero_doppler_time"][-1]))
    _str(ident, "zeroDopplerStartTime", t_start.isoformat())
    _str(ident, "zeroDopplerEndTime", t_end.isoformat())

    # ---- metadata: orbit, attitude ----------------------------------------
    orb_grp = fid.create_group(f"{META}/orbit")
    orbit.save_to_h5(orb_grp)
    _num(orb_grp, "acceleration", np.zeros_like(np.asarray(orbit.velocity)),
         units="meters per second squared", desc="Not provided by SICD; zeros")
    att_grp = fid.create_group(f"{META}/attitude")
    att.save_to_h5(att_grp)
    _str(att_grp, "attitudeType", "Custom",
         desc=f"Antenna-frame to ECEF quaternions (w, x, y, z) derived from {att_source}")
    _num(att_grp, "angularVelocity", np.zeros((att.size, 3)),
         units="radians per second", desc="Not provided by SICD; zeros")

    # ---- swaths ----------------------------------------------------------
    sw = fid.create_group(SWATHS)
    _num(sw, "zeroDopplerTime", g["zero_doppler_time"], units=epoch_attr,
         desc="CF compliant dimension associated with azimuth time")
    _num(sw, "zeroDopplerTimeSpacing", g["dt"], units="seconds",
         desc="Time interval in the along-track direction for raster layers")
    fa = sw.create_group("frequencyA")
    _num(fa, "slantRange", g["slant_range"], units="meters",
         desc="CF compliant dimension associated with slant range")
    _num(fa, "slantRangeSpacing", g["ss_rg"], units="meters")
    _num(fa, "acquiredCenterFrequency", g["fc_acq"], units="Hz")
    _num(fa, "processedCenterFrequency", g["fc_proc"], units="Hz")
    _num(fa, "centerFrequency", g["fc_proc"], units="Hz")
    _num(fa, "acquiredRangeBandwidth", g["bw_acq"], units="Hz")
    _num(fa, "processedRangeBandwidth", g["bw_proc"], units="Hz")
    _num(fa, "rangeBandwidth", g["bw_proc"], units="Hz")
    _num(fa, "nominalAcquisitionPRF", g["prf_acq"], units="Hz",
         desc="Mean slope of Timeline/IPP/Set/IPPPoly; the image grid rate is "
              "1 / zeroDopplerTimeSpacing")
    _num(fa, "chirpDuration", g["chirp_duration"], units="seconds")
    _num(fa, "chirpSlope", g["chirp_slope"], units="Hz per second")
    _num(fa, "sceneCenterGroundRangeSpacing",
         g["ss_rg"] / np.sin(np.radians(g["incidence_deg"])), units="meters")
    _num(fa, "sceneCenterAlongTrackSpacing", g["ss_az"], units="meters")
    _num(fa, "processedAzimuthBandwidth", g["az_bw"], units="Hz")
    _num(fa, "numberOfSubSwaths", 1, dtype="i8")
    fa.create_dataset("listOfPolarizations", data=np.array([g["pol"]], dtype="S2"))
    fa["listOfPolarizations"].attrs["description"] = np.bytes_(
        "List of processed polarization layers with frequency A")

    # ---- processingInformation: zero native-Doppler LUT + inputs ----------
    rg_pad, az_pad = 20 * 1000.0, 20 * 0.25
    lut_rg = np.arange(g["slant_range"][0] - rg_pad,
                       g["slant_range"][-1] + rg_pad + 1000.0, 1000.0)
    lut_az = np.arange(np.floor(g["zero_doppler_time"][0] - az_pad),
                       np.ceil(g["zero_doppler_time"][-1] + az_pad) + 0.25, 0.25)
    params = fid.create_group(f"{META}/processingInformation/parameters")
    for grp in (params, params.create_group("frequencyA")):
        _num(grp, "slantRange", lut_rg, units="meters",
             desc="Slant range dimension corresponding to processing information records")
        _num(grp, "zeroDopplerTime", lut_az, units=epoch_attr,
             desc="Zero doppler time dimension corresponding to processing information records")
    _num(params["frequencyA"], "dopplerCentroid",
         np.zeros((lut_az.size, lut_rg.size)), units="Hz",
         desc="2D LUT of Doppler Centroid for Frequency A -- identically zero: "
              "an RGZERO/INCA grid is a zero-Doppler grid by definition "
              f"(SCPCOA/DopplerConeAng = {sicd.num('SCPCOA/DopplerConeAng'):.9f} deg)")
    inputs = fid.create_group(f"{META}/processingInformation/inputs")
    _str(inputs, "sicdFile", os.path.basename(in_path))
    _str(inputs, "sicdSha256Header",
         hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
         desc="SHA-256 of the SICD XML text as extracted from the NITF")
    _str(inputs, "sicdXml", xml_text, desc="Verbatim SICD XML data extension segment")
    _str(inputs, "sicdVersion", sicd.ns)
    _str(inputs, "sicdGridType", f"{g['grid_type']} / {g['image_type']}")
    _str(inputs, "sicdImageFormAlgo", g["algo"])
    _str(inputs, "sicdProcessingType",
         f"{g['processing_type']} (Applied={g['processing_applied']})")
    _str(inputs, "azimuthFlipped", str(bool(g["flip"])),
         desc="True when sign(RMA/INCA/TimeCAPoly[1]) < 0: NISAR line l = SICD column (NumCols-1-l)")
    _num(inputs, "timeCAPolySlope", float(g["time_ca"][1]), units="seconds per meter")
    _num(inputs, "uColDotVhat", float(g["ucol"] @ _vhat(orbit, t_mid)), units="1",
         desc="Grid/Col/UVectECF . v/|v| at scene centre; -1 for a left-looking INCA grid")
    _str(inputs, "converter", "isce3-benchmark tools/sicd_to_nisar_rslc.py")
    _str(inputs, "isce3Version", isce3.__version__)
    _str(inputs, "nitfImageLayout", json.dumps(layout))

    # ---- calibrationInformation (not read by InSAR; honest constants) ------
    cal = fid.create_group(f"{META}/calibrationInformation/frequencyA")
    nesz = 10.0 ** (g["noise_db"] / 10.0) if g["noise_db"] is not None else 0.0
    for name, val, desc in (
            ("elevationAntennaPattern", 0.0, "Complex two-way elevation antenna pattern (not in SICD; zeros)"),
            ("noiseEquivalentBackscatter", nesz,
             "Noise equivalent backscatter in linear scale (units of DN^2); "
             "10^(Radiometric/NoiseLevel/NoisePoly[0,0] / 10), constant")):
        grp = cal.create_group(name)
        _num(grp, "slantRange", lut_rg, units="meters")
        _num(grp, "zeroDopplerTime", lut_az, units=epoch_attr)
        _num(grp, g["pol"], np.full((lut_az.size, lut_rg.size), val, dtype=np.float32),
             units="1", desc=desc)


def _vhat(orbit, t):
    _, v = orbit.interpolate(float(t))
    v = np.asarray(v)
    return v / np.linalg.norm(v)


def _radar_band(fc: float) -> str:
    for name, lo, hi in (("L", 1e9, 2e9), ("S", 2e9, 4e9), ("C", 4e9, 8e9),
                         ("X", 8e9, 12e9), ("Ku", 12e9, 18e9), ("Ka", 26.5e9, 40e9)):
        if lo <= fc < hi:
            return name
    return "Unknown"


def _circular_centroid(power: np.ndarray) -> float:
    """Power-weighted mean frequency in cycles/pixel, robust to wrap-around."""
    n = power.size
    f = np.fft.fftfreq(n)
    z = np.sum(power * np.exp(2j * np.pi * f))
    return float(np.angle(z) / (2 * np.pi))


def write_image(fid: h5py.File, mm: np.memmap, g: dict, block_lines: int,
                spectrum_lines: int, compress: bool) -> dict:
    """Transpose (and flip) the SICD pixels into the NISAR image; derive valid samples."""
    n_rg, n_az, flip = g["n_rg"], g["n_az"], g["flip"]
    cpx = np.dtype([("r", np.float32), ("i", np.float32)])
    kw = dict(chunks=(512, 512))
    if compress:
        kw.update(compression="gzip", compression_opts=1, shuffle=True)
    img = fid.create_dataset(f"{SWATHS}/frequencyA/{g['pol']}", shape=(n_az, n_rg),
                             dtype=cpx, **kw)
    img.attrs["description"] = np.bytes_(
        f"Focused RSLC image ({g['pol']}), DN as in the SICD (no radiometric scaling)")
    img.attrs["units"] = np.bytes_("1")
    valid = np.zeros((n_az, 2), dtype="i8")

    # Range-spectrum diagnostic on a subset of output lines.
    spec_lines = set(np.linspace(0, n_az - 1, spectrum_lines).round().astype(int).tolist())
    rg_power = np.zeros(n_rg)
    amp_sum = amp_sq = 0.0
    amp_n = 0
    t_start = time.time()
    for l0 in range(0, n_az, block_lines):
        l1 = min(l0 + block_lines, n_az)
        if flip:
            c0, c1 = n_az - l1, n_az - l0          # columns for lines l0..l1-1, reversed
            blk = mm[:, c0:c1, :][:, ::-1, :]
        else:
            blk = mm[:, l0:l1, :]
        blk = np.ascontiguousarray(blk.transpose(1, 0, 2)).astype(np.float32)  # (lines, rg, 2)
        nz = (blk[..., 0] != 0) | (blk[..., 1] != 0)
        any_nz = nz.any(axis=1)
        first = np.argmax(nz, axis=1)
        last = n_rg - np.argmax(nz[:, ::-1], axis=1)
        valid[l0:l1, 0] = np.where(any_nz, first, 0)
        valid[l0:l1, 1] = np.where(any_nz, last, 0)

        for l in range(l0, l1):
            if l in spec_lines:
                z = blk[l - l0, :, 0] + 1j * blk[l - l0, :, 1]
                rg_power += np.abs(np.fft.fft(z)) ** 2
                a = np.abs(z)
                amp_sum += float(a.sum())
                amp_sq += float((a * a).sum())
                amp_n += a.size
        img.write_direct(blk.view(cpx).reshape(l1 - l0, n_rg), dest_sel=np.s_[l0:l1])
        if (l0 // block_lines) % 8 == 0:
            print(f"    lines {l0:6d}..{l1:6d} of {n_az}  ({time.time() - t_start:6.1f} s)",
                  flush=True)

    fa = fid[f"{SWATHS}/frequencyA"]
    ds = fa.create_dataset("validSamplesSubSwath1", data=valid, dtype="i8")
    ds.attrs["description"] = np.bytes_(
        "First and last+1 valid range sample per line, derived from the data "
        "(first/last non-zero I/Q); ImageData/ValidData is not trusted")

    # Azimuth-spectrum diagnostic straight from the range-major array; a flip
    # mirrors the frequency axis, so negate when flipped.
    spec_rows = np.linspace(0, n_rg - 1, spectrum_lines).round().astype(int)
    az_power = np.zeros(n_az)
    for r in spec_rows:
        row = mm[r, :, :].astype(np.float32)
        z = row[:, 0] + 1j * row[:, 1]
        az_power += np.abs(np.fft.fft(z)) ** 2
    # Reported in the SICD column direction, the same direction as the header's
    # DeltaKCOAPoly; the NISAR line direction mirrors it when flipped.
    az_centroid = _circular_centroid(az_power)
    rg_centroid = _circular_centroid(rg_power)

    def frac(x):
        return float(((x + 0.5) % 1.0) - 0.5)

    diag = {
        "amplitude_mean": amp_sum / max(amp_n, 1),
        "amplitude_std": float(np.sqrt(max(amp_sq / max(amp_n, 1) - (amp_sum / max(amp_n, 1)) ** 2, 0.0))),
        "valid_first_min": int(valid[:, 0].min()), "valid_first_max": int(valid[:, 0].max()),
        "valid_last_min": int(valid[:, 1].min()), "valid_last_max": int(valid[:, 1].max()),
        "lines_all_zero": int((~(valid[:, 1] > valid[:, 0])).sum()),
        "range_spectrum": {
            "measured_centroid_cpp": rg_centroid,
            "header_deltakcoa_cpp": frac(g["row_dkcoa"] * g["ss_rg"]),
            "grid_compensated_alt_cpp": frac((g["row_kctr"] + g["row_dkcoa"]) * g["ss_rg"]),
            "bandwidth_cpp": g["row_bw_cpm"] * g["ss_rg"],
            "sgn": g["row_sgn"],
        },
        "azimuth_spectrum": {
            "measured_centroid_cpp_sicd_col_dir": az_centroid,
            "measured_centroid_cpp_nisar_line_dir": -az_centroid if flip else az_centroid,
            "header_deltakcoa_cpp_sicd_col_dir": frac(g["col_dkcoa"] * g["ss_az"]),
            "grid_prf_hz": g["prf_grid"],
            "bandwidth_cpp": g["col_bw_cpm"] * g["ss_az"],
            "sgn": g["col_sgn"],
        },
        "spectrum_lines": len(spec_lines),
        "elapsed_s": time.time() - t_start,
    }
    return diag


def write_bounding_polygon(fid: h5py.File, g: dict, orbit) -> str:
    grid = isce3.product.RadarGridParameters(
        float(g["zero_doppler_time"][0]), g["wavelength"], g["prf_grid"],
        float(g["slant_range"][0]), g["ss_rg"], g["lookside"], g["n_az"], g["n_rg"],
        g["epoch"])
    dem = isce3.geometry.DEMInterpolator(float(g["scp_llh"][2]))
    poly = isce3.geometry.get_geo_perimeter_wkt(grid, orbit, isce3.core.LUT2d(), dem)
    ds = fid[IDENT].create_dataset("boundingPolygon", data=np.bytes_(poly))
    ds.attrs["epsg"] = 4326
    ds.attrs["ogr_geometry"] = np.bytes_("polygon")
    ds.attrs["description"] = np.bytes_(
        f"Perimeter on a constant-height surface at the SCP HAE ({g['scp_llh'][2]:.1f} m)")
    return poly


# --------------------------------------------------------------------------
def summarize(g: dict, orbit, att_source: str) -> dict:
    t0 = g["epoch"] + isce3.core.TimeDelta(float(g["zero_doppler_time"][0]))
    t1 = g["epoch"] + isce3.core.TimeDelta(float(g["zero_doppler_time"][-1]))
    return {
        "grid": f"{g['grid_type']}/{g['image_type']}", "image_form_algo": g["algo"],
        "processing_type": g["processing_type"],
        "shape_az_rg": [g["n_az"], g["n_rg"]], "pol": g["pol"],
        "lookside": g["lookside"], "azimuth_flipped": bool(g["flip"]),
        "ucol_dot_vhat": float(g["ucol"] @ _vhat(orbit, 0.5 * (g["zero_doppler_time"][0] + g["zero_doppler_time"][-1]))),
        "epoch": g["epoch"].isoformat(),
        "zero_doppler_start": t0.isoformat(), "zero_doppler_end": t1.isoformat(),
        "dt_s": g["dt"], "prf_grid_hz": g["prf_grid"], "prf_acq_hz": g["prf_acq"],
        "slant_range_m": [float(g["slant_range"][0]), float(g["slant_range"][-1])],
        "slant_range_spacing_m": g["ss_rg"], "azimuth_spacing_m": g["ss_az"],
        "wavelength_m": g["wavelength"], "fc_proc_hz": g["fc_proc"], "bw_proc_hz": g["bw_proc"],
        "orbit": {"n": g["orbit_n"], "span_s_after_collect_start": list(g["orbit_span"]),
                  "speed_mps": list(g["orbit_speed"]), "radius_km": list(g["orbit_radius_km"])},
        "attitude_source": att_source,
        "scp_llh": list(g["scp_llh"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sicd", help="input SICD NITF")
    ap.add_argument("out", nargs="?", help="output NISAR RSLC HDF5")
    ap.add_argument("--xml", help="take the SICD XML from this file instead of the NITF "
                                  "(dry-run only; lets the RGAZIM rejection be tested)")
    ap.add_argument("--dry-run", action="store_true", help="parse and report only")
    ap.add_argument("--block-lines", type=int, default=512)
    ap.add_argument("--orbit-spacing", type=float, default=0.1, help="state-vector spacing [s]")
    ap.add_argument("--orbit-margin", type=float, default=1.0,
                    help="extend the orbit this far beyond the collect/image span [s]")
    ap.add_argument("--spectrum-lines", type=int, default=256,
                    help="lines/rows used for the spectral-centroid diagnostic")
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    if args.xml:
        xml_text = open(args.xml, encoding="utf-8").read()
        if not args.dry_run:
            raise SystemExit("--xml is only meaningful with --dry-run")
    else:
        xml_text, _ = extract_sicd_xml(args.sicd)
    if not args.dry_run and not args.out:
        raise SystemExit("output path required unless --dry-run")
    if args.out and os.path.exists(args.out) and not args.overwrite:
        raise SystemExit(f"{args.out} exists (use --overwrite)")

    xml_path = os.path.join(os.path.dirname(args.out) if args.out else ".",
                            "._sicd_tmp.xml")
    # Sicd wants a path; keep the accessor from the field-check tool unchanged.
    with open(xml_path, "w", encoding="utf-8") as fh:
        fh.write(xml_text)
    try:
        sicd = Sicd(xml_path)
    finally:
        os.remove(xml_path)

    g = build_grid(sicd)
    orbit = build_orbit(sicd, g, args.orbit_spacing, args.orbit_margin)
    att, att_source = build_attitude(sicd, g, orbit, args.orbit_spacing)
    summary = summarize(g, orbit, att_source)
    print(json.dumps({"parsed": summary}, indent=1))
    if args.dry_run:
        print("DRY-RUN OK")
        return 0

    mm, layout = open_pixels(args.sicd, g["n_rg"], g["n_az"], g["pixel_type"])
    t_start = time.time()
    with h5py.File(args.out, "w", fs_strategy="page", fs_page_size=4194304) as fid:
        write_skeleton(fid, sicd, g, orbit, att, att_source, args.sicd, xml_text, layout)
        print(f"processing polarization {g['pol']} ({g['n_az']}L x {g['n_rg']}P):", flush=True)
        diag = write_image(fid, mm, g, args.block_lines, args.spectrum_lines,
                           not args.no_compress)
        poly = write_bounding_polygon(fid, g, orbit)
        inputs = fid[f"{META}/processingInformation/inputs"]
        _str(inputs, "conversionDiagnostics", json.dumps(diag))
    summary["diagnostics"] = diag
    summary["bounding_polygon"] = poly
    summary["output"] = args.out
    summary["output_bytes"] = os.path.getsize(args.out)
    summary["elapsed_s"] = time.time() - t_start
    print(json.dumps({"converted": summary}, indent=1))
    print("saved file:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
