#!/usr/bin/env python3
"""Run MultiRTC v0.5.4 on a Capella RGZERO SICD as a diagnostic comparator (bench#54).

MultiRTC (github.com/MultiSAR/MultiRTC, BSD-3) is used **read-only**: a
checkout pinned at a0edba80 (v0.5.4) is installed with ``--no-deps`` into a
separate target directory and put on ``PYTHONPATH``; nothing of it is copied
into this repository and the dev image is not modified. It runs against the
same isce3 build as our GCOV path, so both sides share one geocode/RTC core
and a difference between them is an ingest / packaging difference. Prepend
to ``PYTHONPATH``; replacing it drops ``/opt/isce3-build/install/packages``
and silently falls back to the conda-forge isce3 in the image. ``_env()``
records the isce3 version actually imported.

Sub-commands::

    inspect  dump what SicdRzdSlc derives (radar grid, orbit samples,
             wavelength, flip) to JSON, for the radar-domain comparison
    rtc      run multirtc.create_rtc.rtc() for one variant

Variants (all: input beta0, output gamma0, area_projection, biquintic DEM,
rtc_min_value_db -30, rtc_upsampling 2, same DEM file as GCOV):

    stock              MultiRTC's defaults for a SICD (layover/shadow masking on)
    matched            as stock, shadow masking off (GCOV runs without it)
    matched-tfix       as matched, azimuth start time moved to the centre of
                       the first output line (hypothesis 1 below)
    matched-tfix-rfix  as matched-tfix, starting range set to the RGZERO
                       row-0 range R_CA_SCP - SCPPixel.Row * Row.SS
                       (hypothesis 2 below)

The two fixes are runtime subclasses here; MultiRTC's source is untouched.

Hypothesis 1 (``tfix``): at the pinned commit SicdRzdSlc evaluates the "last
column" time at index N (one past the image), which for a time-reversed
azimuth axis makes ``sensing_start`` one line early (1.6101e-4 s on the
2024-06-26 scene).

Hypothesis 2 (``rfix``): ``get_starting_range(0)`` measures the range from the
ARP at closest approach to a point on the plane tangent at the SCP, which
equals the RGZERO row-0 range only at the SCP column; on the 2024-06-26 scene
it is -6.32 m (-10.2 samples) short at column 0, and that value is applied to
the whole grid.

Whether either is visible in the RTC output is a measurement, not a
presumption; the variants only let the effects be separated. MultiRTC's
centre-frequency line (``Min + Max / 2``) gives a wrong wavelength too, but
zero-Doppler geometry and RTC do not depend on the wavelength, so it is
recorded, not patched.

Usage (inside the dev container)::

    PYTHONPATH=/data/external/multirtc-site:$PYTHONPATH python tools/multirtc_capella_rtc.py \\
        inspect /data/capella_mexico_city/<scene>.ntf --out grid.json
    PYTHONPATH=/data/external/multirtc-site:$PYTHONPATH python tools/multirtc_capella_rtc.py \\
        rtc /data/capella_mexico_city/<scene>.ntf --variant matched \\
        --dem /data/capella_mexico_city/dem.tif --resolution 5 --work-dir /out/matched
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
sys.argv = _ARGV

import numpy as np  # noqa: E402

from multirtc.create_rtc import rtc  # noqa: E402
from multirtc.rtc_options import RtcOptions  # noqa: E402
from multirtc.sicd import SicdRzdSlc  # noqa: E402

MULTIRTC_SHA = "a0edba80a05c923b03ffae378e4a1faf293b0f0f"


class SicdRzdSlcStartFix(SicdRzdSlc):
    """SicdRzdSlc with the first output line's time at the centre of SICD column N-1.

    Only the time-reversed case changes (sensing_start moves one line later);
    PRF, starting range, orbit samples and everything else are left as the
    parent computed them.
    """

    def __init__(self, sicd_path: Path):
        super().__init__(sicd_path)
        self.stock_sensing_start = self.sensing_start
        if self.az_reversed:
            n_col = self.shape[1]
            self.sensing_start = self.source.RMA.INCA.TimeCAPoly(
                (n_col - 1 - self.shift[1]) * self.spacing[1])
            self.radar_grid = self.get_radar_grid()


class SicdRzdSlcStartRangeFix(SicdRzdSlcStartFix):
    """As SicdRzdSlcStartFix, plus starting range = R_CA_SCP - SCPPixel.Row * Row.SS."""

    def __init__(self, sicd_path: Path):
        super().__init__(sicd_path)
        self.stock_starting_range = self.starting_range
        self.starting_range = float(self.source.RMA.INCA.R_CA_SCP
                                    - self.shift[0] * self.spacing[0])
        self.radar_grid = self.get_radar_grid()


VARIANT_CLASS = {"stock": SicdRzdSlc, "matched": SicdRzdSlc,
                 "matched-tfix": SicdRzdSlcStartFix,
                 "matched-tfix-rfix": SicdRzdSlcStartRangeFix}


def _grid_dict(slc) -> dict:
    g = slc.radar_grid
    ref = str(g.ref_epoch)
    times = np.linspace(g.sensing_start, g.sensing_stop, 5)
    orbit_samples = []
    for t in times:
        p, v = slc.orbit.interpolate(float(t))
        orbit_samples.append({"t": float(t), "pos": list(map(float, p)), "vel": list(map(float, v))})
    return {
        "multirtc_sha": MULTIRTC_SHA,
        "class": type(slc).__name__,
        "az_reversed": bool(slc.az_reversed),
        "ref_epoch": ref,
        "sensing_start_s": float(g.sensing_start),
        "sensing_stop_s": float(g.sensing_stop),
        "prf_hz": float(g.prf),
        "starting_range_m": float(g.starting_range),
        "range_pixel_spacing_m": float(g.range_pixel_spacing),
        "length": int(g.length), "width": int(g.width),
        "wavelength_m": float(g.wavelength),
        "lookside": str(g.lookside),
        "orbit_samples": orbit_samples,
        "stock_sensing_start_s": float(getattr(slc, "stock_sensing_start", g.sensing_start)),
        "stock_starting_range_m": float(getattr(slc, "stock_starting_range", g.starting_range)),
    }


def _env() -> dict:
    import sarpy
    out = {"isce3": isce3.__version__, "sarpy": sarpy.__version__, "multirtc_sha": MULTIRTC_SHA}
    try:
        out["multirtc_checkout"] = subprocess.run(
            ["git", "-C", "/data/external/MultiRTC-a0edba8", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
    except Exception as exc:  # noqa: BLE001 -- provenance only
        out["multirtc_checkout"] = f"unavailable: {exc}"
    return out


def cmd_inspect(args) -> int:
    out = {"env": _env()}
    for name, cls in (("stock", SicdRzdSlc), ("tfix", SicdRzdSlcStartFix),
                      ("tfix_rfix", SicdRzdSlcStartRangeFix)):
        out[name] = _grid_dict(cls(Path(args.sicd)))
    json.dump(out, open(args.out, "w"), indent=1)
    print(json.dumps(out, indent=1))
    return 0


def cmd_rtc(args) -> int:
    work = Path(args.work_dir)
    (work / "input").mkdir(parents=True, exist_ok=True)
    (work / "output").mkdir(parents=True, exist_ok=True)
    # rtc() writes <stem>_beta0.tif next to the SICD, so give it a private copy
    # of the path (a symlink) inside the work directory.
    link = work / "input" / Path(args.sicd).name
    if not link.exists():
        os.symlink(os.path.abspath(args.sicd), link)
    slc = VARIANT_CLASS[args.variant](link)
    geogrid = slc.create_geogrid(spacing_meters=args.resolution)
    opts = RtcOptions(
        dem_path=str(args.dem), output_dir=str(work / "output"), apply_rtc=True,
        resolution=args.resolution,
        apply_bistatic_delay=slc.supports_bistatic_delay,      # False for SICD
        apply_static_tropo=slc.supports_static_tropo,          # False for SICD
        apply_shadow_masking=(args.variant == "stock"),
    )
    t0 = time.time()
    rtc(slc, geogrid, opts)
    record = {"env": _env(), "variant": args.variant, "resolution_m": args.resolution,
              "dem": str(args.dem), "elapsed_s": time.time() - t0,
              "options": {k: (v if isinstance(v, (bool, int, float, str, type(None))) else str(v))
                          for k, v in vars(opts).items()},
              "grid": _grid_dict(slc),
              "geogrid_requested": {"start_x": geogrid.start_x, "start_y": geogrid.start_y,
                                    "spacing_x": geogrid.spacing_x, "spacing_y": geogrid.spacing_y,
                                    "width": geogrid.width, "length": geogrid.length,
                                    "epsg": geogrid.epsg}}
    json.dump(record, open(work / "run.json", "w"), indent=1)
    print(json.dumps({k: record[k] for k in ("variant", "elapsed_s", "env")}, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inspect")
    i.add_argument("sicd")
    i.add_argument("--out", required=True)
    r = sub.add_parser("rtc")
    r.add_argument("sicd")
    r.add_argument("--variant", choices=tuple(VARIANT_CLASS), required=True)
    r.add_argument("--dem", required=True)
    r.add_argument("--resolution", type=float, default=5.0)
    r.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    return {"inspect": cmd_inspect, "rtc": cmd_rtc}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
