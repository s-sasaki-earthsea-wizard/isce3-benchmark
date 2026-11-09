#!/usr/bin/env python3
"""Explain MultiRTC's flat-plane starting-range shortfall, per scene.

MultiRTC v0.5.4 ``SicdRzdSlc.get_starting_range(col)`` measures from the ARP at
the column's closest-approach time to ``SCP + uRow * xrow + uCol * ycol``, a
point on the plane through the SCP spanned by ``Grid.Row.UVectECF`` and
``Grid.Col.UVectECF``. In an RGZERO / INCA grid the row-0 pixels instead share
one closest-approach range, ``R_CA_SCP - (SCPPixel.Row - FirstRow) * Row.SS``.

Second-order expansion (the main check). Write the code's distance as
``f(y) = |D(y)|`` with ``D(y) = ARP(T(y)) - P(y)``, ``T = TimeCAPoly`` and
``P(y)`` the plane point at along-track coordinate ``y = ycol``. At the SCP
column ``f(0)`` is the row-0 range and ``D . D' = 0`` (zero Doppler), so

    f(y) - f(0) ~= y**2 / 2 * (|D'|**2 + D . D'') / f(0)

with ``D' = V * T1 - uCol`` and ``D'' = A * T1**2 (+ 2 V T2)``, where ``V`` and
``A`` are the ARP velocity and acceleration from the SICD's ECF ``ARPPoly`` and
``T1``, ``T2`` the ``TimeCAPoly`` coefficients. The two terms read as:

* fall: ``-1/2 * a_los * dt**2``, the ARP's ECF acceleration toward the scene
  along the line of sight over the time ``dt = T1 * y`` from the SCP column;
* speed: ``+(k - 1)**2 * y**2 / (2 R)`` when ``V * T1`` is parallel to ``uCol``,
  ``k`` being the metres the ARP moves per metre of the plane line.

Only SICD metadata in ECF enter; no Earth model is used.

Toy model (kept for reference): a circular orbit and no Earth rotation, in
which the row-0 points lie on a circle about the orbit axis at the SCP's
distance ``rho`` and the plane leaves it by ``y**2 / (2 * rho) * cos(a)`` on
the line of sight. It ignores Earth rotation and orbit eccentricity and lands
within about 11 % of the code's value.

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


def _second_order(s, u_row: np.ndarray, u_col: np.ndarray, xrow: float) -> dict:
    """Expand MultiRTC's flat-plane distance to second order about the SCP column.

    Args:
        s: sarpy SICD structure.
        u_row: ``Grid.Row.UVectECF``.
        u_col: ``Grid.Col.UVectECF``.
        xrow: Row coordinate of row 0 [m].

    Returns:
        Expansion coefficients and their physical reading.
    """
    tca = np.asarray(s.RMA.INCA.TimeCAPoly.Coefs, dtype=float)
    t1 = float(tca[1])
    t2 = float(tca[2]) if tca.size > 2 else 0.0
    t0 = float(tca[0])
    arp = np.asarray(s.Position.ARPPoly(t0), dtype=float)
    vel = np.asarray(s.Position.ARPPoly.derivative_eval(t0, 1), dtype=float)
    acc = np.asarray(s.Position.ARPPoly.derivative_eval(t0, 2), dtype=float)
    plane0 = s.GeoData.SCP.ECF.get_array() + u_row * xrow

    d0 = arp - plane0
    d1 = vel * t1 - u_col
    d2 = acc * t1 * t1 + 2.0 * vel * t2
    f0 = float(np.linalg.norm(d0))
    to_scene = -d0 / f0
    return {
        "time_ca_scp_s": t0,
        "f0_m": f0,
        "first_order_m_per_m": float(d0 @ d1 / f0),
        "curvature_per_m": float((d1 @ d1 + d0 @ d2) / f0 - (d0 @ d1) ** 2 / f0**3),
        "fall_per_m2": float(0.5 * (d0 @ (acc * t1 * t1)) / f0),
        "speed_per_m2": float(0.5 * (d1 @ d1) / f0),
        "arp_accel_toward_scene_m_s2": float(acc @ to_scene),
        "arp_metres_per_plane_metre": float(abs(t1) * np.linalg.norm(vel)),
        "arp_velocity_dot_ucol": float(_unit(vel * t1) @ u_col),
    }


def curvature(path: Path) -> dict:
    """Compare MultiRTC's flat-plane starting range with its second-order expansion.

    Args:
        path: SICD file with an RGZERO grid.

    Returns:
        Per-scene geometry, the expansion and toy-model inputs, and per-column
        values of MultiRTC's shortfall and both predictions, in metres.
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

    xrow = -shift_row * ss_row
    r_row0 = float(s.RMA.INCA.R_CA_SCP + xrow)
    ex = _second_order(s, u_row, u_col, xrow)
    t1 = float(s.RMA.INCA.TimeCAPoly.Coefs[1])

    cols = sorted({0, n_col // 4, int(round(shift_col)), (3 * n_col) // 4, n_col - 1})
    per_col = []
    for c in cols:
        y = (c - shift_col) * ss_col
        per_col.append({
            "col": c,
            "ycol_m": y,
            "dt_from_scp_col_s": t1 * y,
            "multirtc_minus_rgzero_m": float(slc.get_starting_range(c) - r_row0),
            "second_order_m": float(ex["f0_m"] - r_row0 + ex["first_order_m_per_m"] * y
                                    + 0.5 * ex["curvature_per_m"] * y * y),
            "fall_m": float(ex["fall_per_m2"] * y * y),
            "speed_m": float(ex["speed_per_m2"] * y * y),
            "toy_model_m": float(-(y * y) / (2.0 * rho) * cos_a),
        })
    col0 = per_col[0]
    return {
        "scene": path.stem,
        "side_of_track": s.SCPCOA.SideOfTrack,
        "num_cols": n_col,
        "col_ss_m": ss_col,
        "time_ca_poly_slope_s_per_m": t1,
        "ucol_dot_velocity_unit": float(u_col @ _unit(vel)),
        "urow_dot_los": float(u_row @ los),
        "incidence_deg": incidence,
        "rgzero_row0_m": r_row0,
        "second_order": ex,
        "toy_model": {"rho_m": rho, "radial_to_los_angle_deg": float(np.degrees(np.arccos(cos_a)))},
        "per_column": per_col,
        "col0_second_order_over_multirtc": col0["second_order_m"] / col0["multirtc_minus_rgzero_m"],
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
    hdr = ("scene", "side", "uCol.v", "a_los[m/s2]", "k", "dt0[s]", "fall0[m]", "speed0[m]",
           "col0 MultiRTC[m]", "col0 2nd[m]", "2nd/MultiRTC", "toy/MultiRTC", "col0 ground[m]")
    print(" | ".join(hdr))
    for r in rows:
        c0 = r["per_column"][0]
        ex = r["second_order"]
        print(" | ".join([
            r["scene"].replace("CAPELLA_", ""), r["side_of_track"],
            f"{r['ucol_dot_velocity_unit']:+.4f}", f"{ex['arp_accel_toward_scene_m_s2']:.3f}",
            f"{ex['arp_metres_per_plane_metre']:.4f}", f"{c0['dt_from_scp_col_s']:+.3f}",
            f"{c0['fall_m']:+.3f}", f"{c0['speed_m']:+.3f}",
            f"{c0['multirtc_minus_rgzero_m']:+.4f}", f"{c0['second_order_m']:+.4f}",
            f"{r['col0_second_order_over_multirtc']:.5f}", f"{r['col0_toy_over_multirtc']:.3f}",
            f"{r['col0_ground_m']:+.2f}"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
