#!/usr/bin/env python3
"""Survey MultiRTC's RGZERO radar grid against the SICD definition, per scene.

For each SICD, builds MultiRTC's own ``SicdRzdSlc`` (pinned v0.5.4, read-only)
and compares the radar-grid quantities it derives with values taken directly
from the SICD metadata:

* sensing start -- MultiRTC evaluates ``TimeCAPoly`` at column N (one past the
  image) for the "last column"; with a time-reversed azimuth axis that value
  becomes ``sensing_start``. Reference: the time of column N-1 when reversed,
  of column 0 otherwise. Reported in lines (x PRF).
* starting range -- MultiRTC's ``get_starting_range(col)`` (ARP at closest
  approach to a point on the SCP tangent plane) vs the RGZERO row-0 range
  ``R_CA_SCP - (SCPPixel.Row - FirstRow) * Row.SS``, at five columns; the
  column-0 value is what MultiRTC uses for the whole grid.
* wavelength -- MultiRTC's ``Min + Max / 2`` vs c / ((Min + Max) / 2).
* CollectStart -- the sub-microsecond part in the SICD XML, which sarpy's
  default microsecond parsing (and so MultiRTC's reference epoch) drops.

No pixels are read. Usage (inside the dev container)::

    PYTHONPATH=/data/external/multirtc-site:$PYTHONPATH \\
        python tools/multirtc_grid_survey.py SICD [SICD ...] --out survey.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
sys.argv = _ARGV

import numpy as np  # noqa: E402
import sarpy  # noqa: E402

from multirtc.sicd import SicdRzdSlc  # noqa: E402

MULTIRTC_SHA = "a0edba80a05c923b03ffae378e4a1faf293b0f0f"
C = isce3.core.speed_of_light


_COLLECT_START = re.compile(rb"<(?:\w+:)?CollectStart>([^<]+)</(?:\w+:)?CollectStart>")


def _raw_collect_start(path: Path) -> str | None:
    """Timeline/CollectStart as written in the file's SICD XML (DES segment).

    NITF puts data extension segments after the image, so the tail is searched
    first; the whole file is scanned only if the tail has no match.
    """
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - (8 << 20)))
        m = _COLLECT_START.search(fh.read())
        if m is None:
            fh.seek(0)
            m = _COLLECT_START.search(fh.read())
    return m.group(1).decode() if m else None


def _collect_start(path: Path, parsed) -> dict:
    """CollectStart in the XML vs as sarpy parsed it, and the nanoseconds dropped."""
    text = _raw_collect_start(path)
    frac = text.rstrip("Z").split(".")[1] if text and "." in text else ""
    dropped_ns = int(frac[6:9].ljust(3, "0")) if len(frac) > 6 else 0
    return {"xml": text, "sarpy": str(parsed), "dropped_ns": dropped_ns}


def survey(path: Path) -> dict:
    slc = SicdRzdSlc(path)
    s = slc.source
    n_row, n_col = slc.shape
    ss_row, ss_col = slc.spacing
    shift_row, shift_col = slc.shift
    tca = s.RMA.INCA.TimeCAPoly

    def col_time(col: float) -> float:
        return float(tca((col - shift_col) * ss_col))

    t0, t_last, t_n = col_time(0), col_time(n_col - 1), col_time(n_col)
    reversed_axis = t_n < t0
    ref_start = t_last if reversed_axis else t0
    dt_line = abs(t_last - t0) / (n_col - 1)

    r_row0 = float(s.RMA.INCA.R_CA_SCP - shift_row * ss_row)
    cols = sorted({0, n_col // 4, int(round(shift_col)), (3 * n_col) // 4, n_col - 1})
    per_col = [{"col": c, "ycol_km": (c - shift_col) * ss_col / 1e3,
                "multirtc_minus_rgzero_m": float(slc.get_starting_range(c) - r_row0)}
               for c in cols]

    f_min, f_max = s.RadarCollection.TxFrequency.Min, s.RadarCollection.TxFrequency.Max
    return {
        "scene": path.stem,
        "side_of_track": s.SCPCOA.SideOfTrack,
        "shape_rows_cols": [n_row, n_col],
        "grid_type": s.Grid.Type,
        "time_ca_poly_slope": float(tca.Coefs[1]),
        "multirtc_az_reversed": bool(slc.az_reversed),
        "sensing_start": {
            "multirtc_s": float(slc.sensing_start), "sicd_s": ref_start,
            "diff_lines": float((slc.sensing_start - ref_start) / dt_line)},
        "prf": {"multirtc_hz": float(slc.prf), "sicd_hz": 1.0 / dt_line,
                "ratio": float(slc.prf * dt_line)},
        "starting_range": {
            "multirtc_m": float(slc.starting_range), "rgzero_row0_m": r_row0,
            "diff_m": float(slc.starting_range - r_row0),
            "diff_samples": float((slc.starting_range - r_row0) / ss_row),
            "per_column": per_col},
        "wavelength": {
            "multirtc_m": float(slc.wavelength),
            "tx_centre_m": float(C / ((f_min + f_max) / 2)),
            "multirtc_centre_ghz": float((f_min + f_max / 2) / 1e9),
            "tx_centre_ghz": float((f_min + f_max) / 2 / 1e9)},
        "collect_start": _collect_start(path, s.Timeline.CollectStart),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sicd", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = [survey(p) for p in args.sicd]
    out = {"env": {"isce3": isce3.__version__, "sarpy": sarpy.__version__,
                   "multirtc_sha": MULTIRTC_SHA, "numpy": np.__version__},
           "scenes": rows}
    args.out.write_text(json.dumps(out, indent=1))
    hdr = ("scene", "side", "cols", "slope", "start[lines]", "prf ratio",
           "R0 diff[m]", "R0 diff[smp]", "lambda MRTC/tx [cm]", "dropped ns")
    print(" | ".join(hdr))
    for r in rows:
        print(" | ".join([
            r["scene"].replace("CAPELLA_", ""), r["side_of_track"], str(r["shape_rows_cols"][1]),
            f"{r['time_ca_poly_slope']:+.3e}", f"{r['sensing_start']['diff_lines']:+.3f}",
            f"{r['prf']['ratio']:.6f}", f"{r['starting_range']['diff_m']:+.3f}",
            f"{r['starting_range']['diff_samples']:+.2f}",
            f"{100 * r['wavelength']['multirtc_m']:.3f}/{100 * r['wavelength']['tx_centre_m']:.3f}",
            str(r["collect_start"]["dropped_ns"])]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
