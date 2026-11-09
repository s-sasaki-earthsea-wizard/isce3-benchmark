#!/usr/bin/env python3
"""Toy-model check of MultiRTC's flat-plane starting-range shortfall, per scene.

MultiRTC v0.5.4 ``SicdRzdSlc.get_starting_range(col)`` measures from the ARP at
the column's closest-approach time to ``SCP + uRow * xrow + uCol * ycol``, a
point on the plane through the SCP spanned by ``Grid.Row.UVectECF`` and
``Grid.Col.UVectECF``. In an RGZERO / INCA grid the row-0 pixels instead share
one closest-approach range, ``R_CA_SCP - (SCPPixel.Row - FirstRow) * Row.SS``.

Toy model: a circular orbit and no Earth rotation. Rotating the ARP and a
target together about the orbit axis keeps their distance, so the row-0 points
lie on a circle about that axis, at the SCP's distance ``rho`` from it. The
plane's straight line is tangent to that circle at the SCP column and leaves it
outward by ``y**2 / (2 * rho)`` at along-track distance ``y``; on the line of
sight this predicts a shortfall of ``y**2 / (2 * rho) * cos(a)``, where ``a`` is
the angle between the outward radial direction and the direction to the ARP.
The model is evaluated at the SCP's closest-approach time from the SICD's own
``ARPPoly`` and compared with MultiRTC's ``get_starting_range`` at five columns.

Also recorded per scene: the sign of ``Col.UVectECF`` against the ARP velocity
(which sets the sign of the ``TimeCAPoly`` slope) and the slant-to-ground factor
``1 / sin(IncidenceAng)`` for the column-0 shortfall.

No pixels are read. Usage (inside the dev container)::

    PYTHONPATH=/data/external/multirtc-site:$PYTHONPATH \\
        python tools/multirtc_plane_curvature.py SICD [SICD ...] --out curvature.json

``make multirtc-plane-curvature`` runs it on every Capella SICD on disk against
the read-only v0.5.4 pin.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# isce3 parses sys.argv at import time; hide our arguments from it.
_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import isce3  # noqa: E402
sys.argv = _ARGV

import numpy as np  # noqa: E402
import sarpy  # noqa: E402

from multirtc.sicd import SicdRzdSlc  # noqa: E402
from multirtc_provenance import multirtc_provenance  # noqa: E402


def _unit(v: np.ndarray) -> np.ndarray:
    """Return ``v`` scaled to unit length."""
    return v / np.linalg.norm(v)


def curvature(path: Path) -> dict:
    """Compare MultiRTC's flat-plane starting range with the toy model.

    Args:
        path: SICD file with an RGZERO grid.

    Returns:
        Per-scene geometry, the toy-model inputs, and per-column values of
        MultiRTC's shortfall and the toy-model prediction, in metres.
    """
    slc = SicdRzdSlc(path)
    s = slc.source
    n_col = slc.shape[1]
    ss_row, ss_col = slc.spacing
    shift_row, shift_col = slc.shift

    t_scp = float(s.SCPCOA.SCPTime)
    arp = np.asarray(s.Position.ARPPoly(t_scp), dtype=float)
    vel = np.asarray(s.Position.ARPPoly.derivative_eval(t_scp), dtype=float)
    scp = s.GeoData.SCP.ECF.get_array()
    u_row = s.Grid.Row.UVectECF.get_array()
    u_col = s.Grid.Col.UVectECF.get_array()
    los = _unit(scp - arp)  # from the ARP to the SCP

    axis = _unit(np.cross(arp, vel))  # orbit normal through the Earth centre
    scp_in_plane = scp - (scp @ axis) * axis
    rho = float(np.linalg.norm(scp_in_plane))
    cos_a = float(_unit(scp_in_plane) @ -los)
    incidence = float(s.SCPCOA.IncidenceAng)

    r_row0 = float(s.RMA.INCA.R_CA_SCP - shift_row * ss_row)
    cols = sorted({0, n_col // 4, int(round(shift_col)), (3 * n_col) // 4, n_col - 1})
    per_col = []
    for c in cols:
        y = (c - shift_col) * ss_col
        per_col.append({
            "col": c,
            "ycol_m": y,
            "multirtc_minus_rgzero_m": float(slc.get_starting_range(c) - r_row0),
            "toy_model_m": float(-(y * y) / (2.0 * rho) * cos_a),
        })
    col0 = per_col[0]
    return {
        "scene": path.stem,
        "side_of_track": s.SCPCOA.SideOfTrack,
        "num_cols": n_col,
        "col_ss_m": ss_col,
        "time_ca_poly_slope_s_per_m": float(s.RMA.INCA.TimeCAPoly.Coefs[1]),
        "ucol_dot_velocity_unit": float(u_col @ _unit(vel)),
        "urow_dot_los": float(u_row @ los),
        "rho_m": rho,
        "radial_to_los_angle_deg": float(np.degrees(np.arccos(cos_a))),
        "incidence_deg": incidence,
        "rgzero_row0_m": r_row0,
        "per_column": per_col,
        "col0_toy_over_multirtc": col0["toy_model_m"] / col0["multirtc_minus_rgzero_m"],
        "col0_ground_m": col0["multirtc_minus_rgzero_m"] / np.sin(np.radians(incidence)),
    }


def main() -> int:
    """Run the check on every SICD given and write one JSON file."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sicd", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = [curvature(p) for p in args.sicd]
    out = {"env": {"isce3": isce3.__version__, "sarpy": sarpy.__version__,
                   "numpy": np.__version__, **multirtc_provenance()},
           "scenes": rows}
    args.out.write_text(json.dumps(out, indent=1))
    hdr = ("scene", "side", "uCol.v", "rho[km]", "angle[deg]", "inc[deg]",
           "col0 MultiRTC[m]", "col0 toy[m]", "toy/MultiRTC", "col0 ground[m]")
    print(" | ".join(hdr))
    for r in rows:
        c0 = r["per_column"][0]
        print(" | ".join([
            r["scene"].replace("CAPELLA_", ""), r["side_of_track"],
            f"{r['ucol_dot_velocity_unit']:+.4f}", f"{r['rho_m'] / 1e3:.1f}",
            f"{r['radial_to_los_angle_deg']:.2f}", f"{r['incidence_deg']:.2f}",
            f"{c0['multirtc_minus_rgzero_m']:+.3f}", f"{c0['toy_model_m']:+.3f}",
            f"{r['col0_toy_over_multirtc']:.3f}", f"{r['col0_ground_m']:+.2f}"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
