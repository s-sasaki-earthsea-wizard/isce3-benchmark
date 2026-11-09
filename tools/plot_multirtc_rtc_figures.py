#!/usr/bin/env python3
"""Figures 3 and 4 of ``reports/2026-09-capella-rtc-gcov-vs-multirtc.md`` §3.2.

Figure 3 (SVG) shows the MultiRTC-vs-GCOV map offset, predicted and measured,
and the gamma0 agreement, one radar-grid fix at a time. It is drawn from the
committed comparison JSONs only (``artifacts/sicd-capella-rtc-20260924/``).

Figure 4 (PNG) shows one 400 m chip of geocoded gamma0: isce3 GCOV alone, then
GCOV (magenta) over MultiRTC (green) for stock-grid and fully fixed MultiRTC.
Where the two agree the overlay is gray; a shift shows as magenta and green
fringes on opposite sides of each bright feature. The chip is chosen
automatically as the fully valid 80 x 80 px window with the largest mean
gradient toward near range of 5 x 5-smoothed GCOV gamma0 in dB, so that edges
cross the direction of the shift. It needs the rasters on disk.

Usage (inside the dev container)::

    python tools/plot_multirtc_rtc_figures.py --out-dir reports/figures

``make multirtc-rtc-figures`` runs it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_rtc import _overlap, read_gcov, read_tif  # noqa: E402

STEM = "CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055"
STEPS = [("matched", "v0.5.4\ngrid"),
         ("matched-tfix", "+ start-\ntime fix"),
         ("matched-tfix-rfix", "+ starting-\nrange fix")]
REPORT = "2026-09-capella-rtc-gcov-vs-multirtc"

INK, MUTED, AXIS, GRID, SURFACE = "#1f2328", "#57606a", "#8c959f", "#d0d7de", "#ffffff"
HUE = "#0969da"
SWATCH_GCOV, SWATCH_MULTIRTC = "#b000b0", "#00b000"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none",
    "text.color": INK, "axes.labelcolor": INK, "axes.edgecolor": AXIS,
    "axes.linewidth": 0.8, "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelcolor": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
})


def _load_steps(json_dir: Path) -> list[dict]:
    """Read the predicted / measured offsets and agreement for each step.

    Args:
        json_dir: Directory holding ``20240626_<variant>.json``.

    Returns:
        One dict per step, offsets in metres (near range positive).
    """
    rows = []
    for variant, label in STEPS:
        d = json.loads((json_dir / f"20240626_{variant}.json").read_text())
        p = d["predicted_offset_from_grid"]
        m = d["geocoded"]["offset_multirtc_minus_gcov"]
        rows.append({
            "variant": variant, "label": label,
            "pred_along": p["median_along_track_m"],
            "pred_near": -p["median_across_track_far_positive_m"],
            "meas_along": m["median_along_track_m"],
            "meas_near": -m["median_across_track_far_positive_m"],
            "agree_pct": 100.0 * d["geocoded"]["radiometry_db"]["frac_abs_lt_0.01db"],
            "heading_deg": m["flight_heading_deg_from_north"],
        })
    return rows


def _offset_panel(ax, rows: list[dict], key: str, title: str, ylim: tuple) -> None:
    """Draw predicted (hollow) and measured (filled) offsets per step."""
    x = np.arange(len(rows))
    ax.axhline(0.0, color=AXIS, linewidth=1.0, zorder=1)
    ax.plot(x - 0.1, [r[f"pred_{key}"] for r in rows], "o", ms=8, mfc=SURFACE, mec=HUE,
            mew=2, ls="none", zorder=3)
    ax.plot(x + 0.1, [r[f"meas_{key}"] for r in rows], "o", ms=8, mfc=HUE, mec=SURFACE,
            mew=2, ls="none", zorder=3)
    ax.set_xticks(x, [r["label"] for r in rows])
    ax.set_xlim(-0.5, len(rows) - 0.5)
    ax.set_ylim(*ylim)
    ax.set_title(title, pad=10)
    ax.tick_params(axis="x", length=0)


def figure_offsets(rows: list[dict], out: Path) -> None:
    """Write Figure 3: offsets and agreement, one fix at a time.

    Args:
        rows: Output of ``_load_steps``.
        out: SVG path.
    """
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 4.4), gridspec_kw={"wspace": 0.3})
    fig.subplots_adjust(top=0.74)
    a_along, a_near, a_agree = axes

    _offset_panel(a_along, rows, "along", "Along-track offset [m]", (-1.45, 0.55))
    d_along = rows[1]["pred_along"] - rows[0]["pred_along"]
    a_along.annotate("", xy=(0.88, rows[1]["pred_along"]), xytext=(0.12, rows[0]["pred_along"]),
                     arrowprops={"arrowstyle": "->", "color": MUTED, "lw": 1.2})
    a_along.text(0.62, -0.78, f"start-time fix: {d_along:+.3f} m\n= one line (Col.SS 1.0625 m)",
                 ha="left", va="center", fontsize=9, color=MUTED)

    _offset_panel(a_near, rows, "near", "Offset toward near range [m]", (-1.0, 11.0))
    d_near = rows[2]["pred_near"] - rows[1]["pred_near"]
    a_near.annotate("", xy=(1.88, 0.6), xytext=(1.12, rows[1]["pred_near"] - 0.4),
                    arrowprops={"arrowstyle": "->", "color": MUTED, "lw": 1.2})
    a_near.text(1.42, 3.4, "starting-range fix\n" + f"{d_near:+.2f} m".replace("-", "−"),
                ha="right", va="center", fontsize=9, color=MUTED)
    for i, r in enumerate(rows[:2]):
        a_near.text(i + 0.22, r["meas_near"], f"{r['meas_near']:.2f}", va="center", fontsize=9)
        a_near.text(i - 0.22, r["pred_near"], f"{r['pred_near']:.2f}", va="center", ha="right",
                    fontsize=9)

    x = np.arange(len(rows))
    vals = [r["agree_pct"] for r in rows]
    a_agree.bar(x, vals, width=0.22, color=HUE, zorder=2)
    for i, v in enumerate(vals):
        a_agree.text(i, v + 2.5, f"{v:.2f} %", ha="center", va="bottom", fontsize=9)
    a_agree.set_xticks(x, [r["label"] for r in rows])
    a_agree.set_xlim(-0.5, len(rows) - 0.5)
    a_agree.set_ylim(0, 112)
    a_agree.set_yticks([0, 25, 50, 75, 100])
    a_agree.tick_params(axis="x", length=0)
    a_agree.set_title("gamma0 within 0.01 dB [%]", pad=10)

    handles = [
        Line2D([], [], marker="o", ms=8, mfc=SURFACE, mec=HUE, mew=2, ls="none",
               label="predicted: rdr2geo of 25 pixels through both radar grids"),
        Line2D([], [], marker="o", ms=8, mfc=HUE, mec=HUE, ls="none",
               label="measured: median of 99 tile phase correlations (biased estimator, §3.2)"),
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.115, 0.905), ncol=2,
               frameon=False, fontsize=9.5, handletextpad=0.4, columnspacing=1.6)
    fig.suptitle("MultiRTC minus isce3 GCOV on the Mexico City SICD, one radar-grid fix at a time",
                 x=0.115, y=0.985, ha="left", fontsize=12, fontweight="bold")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


def _box(a: np.ndarray, k: int) -> np.ndarray:
    """Return the k x k moving average of ``a`` (edges shrink the window)."""
    out = a.copy()
    for axis in (0, 1):
        c = np.cumsum(np.pad(out, [(k // 2 + 1, k // 2) if ax == axis else (0, 0)
                                   for ax in (0, 1)], mode="edge"), axis=axis)
        hi = np.take(c, np.arange(k, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - k), axis=axis)
        out = (hi - lo) / k
    return out


def _chip(gcov_db: np.ndarray, valid: np.ndarray, size: int, stride: int,
          direction: np.ndarray) -> tuple[int, int]:
    """Return the top-left (row, col) of the fully valid window with the most edges.

    Edges count by the gradient along ``direction`` (east, north), so the chip
    shows a shift in that direction as fringes rather than as moved texture.
    """
    g = _box(np.clip(gcov_db, -25.0, 10.0), 5)  # structure, not speckle
    grad = np.zeros_like(g)
    d_east = g[1:-1, 2:] - g[1:-1, :-2]
    d_north = g[:-2, 1:-1] - g[2:, 1:-1]  # rows run north to south
    grad[1:-1, 1:-1] = np.abs(d_east * direction[0] + d_north * direction[1])
    best, pos = -1.0, (0, 0)
    for r0 in range(0, g.shape[0] - size, stride):
        for c0 in range(0, g.shape[1] - size, stride):
            if not valid[r0:r0 + size, c0:c0 + size].all():
                continue
            score = float(grad[r0:r0 + size, c0:c0 + size].mean())
            if score > best:
                best, pos = score, (r0, c0)
    return pos


def figure_chips(data: Path, heading_deg: float, out: Path, size: int = 80) -> dict:
    """Write Figure 4: one chip of GCOV and the two MultiRTC overlays.

    Args:
        data: ``/data/capella_mexico_city`` (GCOV and MultiRTC runs below it).
        heading_deg: Flight heading from north, for the direction key.
        out: PNG path.
        size: Chip size in pixels.

    Returns:
        The chip location, for the record.
    """
    gc = read_gcov(str(data / "gcov/20240626/gcov_20240626.h5"))
    runs = {}
    for variant in ("matched", "matched-tfix-rfix"):
        b, xb, yb = read_tif(str(data / f"multirtc/20240626/{variant}/output/{STEM}.tif"))
        runs[variant] = (b, *_overlap(gc["x"], gc["y"], xb, yb))
    # the runs cover slightly different areas; keep the part all three share
    rows = (max(r[1][0].start for r in runs.values()), min(r[1][0].stop for r in runs.values()))
    cols = (max(r[1][1].start for r in runs.values()), min(r[1][1].stop for r in runs.values()))
    sg = (slice(*rows), slice(*cols))
    layers = {}
    for variant, (b, g, m) in runs.items():
        dr, dc = m[0].start - g[0].start, m[1].start - g[1].start
        layers[variant] = b[rows[0] + dr:rows[1] + dr, cols[0] + dc:cols[1] + dc].astype(np.float64)
    a = gc["gamma0"][sg].astype(np.float64)
    ok = np.isfinite(a) & (a > 0) & (gc["mask"][sg] >= 1) & (gc["mask"][sg] <= 254)
    for b in layers.values():
        ok &= np.isfinite(b) & (b > 0)

    def db(v):
        return 10.0 * np.log10(np.where(ok, v, 1.0))

    a_db = db(a)
    h = np.radians(heading_deg)
    along = np.array([np.sin(h), np.cos(h)])
    near = np.array([along[1], -along[0]])  # right of track: toward near range when left-looking
    assert gc["y"][1] < gc["y"][0], "expected north-up rows"
    r0, c0 = _chip(a_db, ok, size, stride=10, direction=near)
    win = (slice(r0, r0 + size), slice(c0, c0 + size))
    lo, hi = np.percentile(a_db[win], [2, 98])

    def norm(v):
        return np.clip((v - lo) / (hi - lo), 0.0, 1.0)

    ga = norm(a_db[win])
    x = gc["x"][sg[1]][c0:c0 + size]
    y = gc["y"][sg[0]][r0:r0 + size]
    px = float(x[1] - x[0])
    extent = [0, size * px, 0, size * px]

    fig, axes = plt.subplots(1, 3, figsize=(12.4, 4.9), gridspec_kw={"wspace": 0.06})
    panels = [("isce3 GCOV gamma0", np.dstack([ga, ga, ga]))]
    for variant, title in (("matched", "GCOV over MultiRTC, v0.5.4 grid"),
                           ("matched-tfix-rfix", "GCOV over MultiRTC, both grid fixes")):
        gb = norm(db(layers[variant])[win])
        panels.append((title, np.dstack([ga, gb, ga])))
    for ax, (title, rgb) in zip(axes, panels):
        ax.imshow(rgb, extent=extent, interpolation="nearest")
        ax.set_title(title, pad=8)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(True)
            sp.set_color(AXIS)
            sp.set_linewidth(0.8)

    ax0 = axes[0]
    ax0.add_patch(plt.Rectangle((14, 12), 130, 34, color=SURFACE, alpha=0.85, lw=0))
    ax0.plot([24, 124], [24, 24], color=INK, lw=3, solid_capstyle="butt")
    ax0.text(74, 30, "100 m", ha="center", va="bottom", fontsize=9)
    w = size * px
    ax0.add_patch(plt.Rectangle((w - 158, w - 112), 150, 104, color=SURFACE, alpha=0.88, lw=0))
    origin = np.array([w - 146, w - 60])
    for vec, lab in ((along, "flight"), (near, "near range")):
        tip = origin + 38 * vec
        ax0.annotate("", xy=tip, xytext=origin,
                     arrowprops={"arrowstyle": "->", "color": INK, "lw": 1.4})
        ax0.text(tip[0] + 5, tip[1], lab, ha="left", va="center", fontsize=8.5)

    handles = [Patch(color=SWATCH_GCOV, label="brighter in GCOV"),
               Patch(color=SWATCH_MULTIRTC, label="brighter in MultiRTC"),
               Patch(color="#9a9a9a", label="equal")]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.385, 0.175), ncol=3,
               frameon=False, fontsize=9.5, handlelength=1.2)
    fig.text(0.125, 0.08,
             f"5 m pixels, {size} × {size} px chip at UTM E {x[0]:.0f}, N {y[0]:.0f} (upper left); "
             f"stretch {lo:.1f} to {hi:.1f} dB from GCOV, same for both layers.",
             ha="left", va="bottom", fontsize=9, color=MUTED)
    fig.suptitle("Geocoded gamma0 on one 400 m chip of the Mexico City scene",
                 x=0.125, y=0.95, ha="left", fontsize=12, fontweight="bold")
    fig.savefig(out, dpi=110, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return {"chip_row": r0, "chip_col": c0, "size_px": size, "utm_e_upper_left": float(x[0]),
            "utm_n_upper_left": float(y[0]), "stretch_db": [float(lo), float(hi)]}


def main() -> int:
    """Write both figures."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json-dir", type=Path, default=Path("artifacts/sicd-capella-rtc-20260924"))
    ap.add_argument("--data", type=Path, default=Path("/data/capella_mexico_city"))
    ap.add_argument("--out-dir", type=Path, default=Path("reports/figures"))
    args = ap.parse_args()
    rows = _load_steps(args.json_dir)
    figure_offsets(rows, args.out_dir / f"{REPORT}-3.svg")
    chip = figure_chips(args.data, rows[0]["heading_deg"], args.out_dir / f"{REPORT}-4.png")
    print(json.dumps({"steps": rows, "chip": chip}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
