#!/usr/bin/env python3
"""Sensitivity harness for the isce3 rubbersheet offsets polyfit.

Made for the localization study of isce3-benchmark issue #26. The
CPU-vs-GPU difference of the RIFG ``pixelOffsets`` layer was shown to
be *exactly* a difference of two degree-2 polynomial surfaces
(quad-fit residual 1.7e-9 px against a 3.5e-2 px field), i.e. a
coefficient difference of the production rubbersheet robust fit
(``run_rubbersheet_with_polyfit`` -> ``offsets_polyfit``). This
harness probes *why* the coefficients can differ: it perturbs the fit
inputs by physically observed scales (single-window peak flips,
1/32 px quantization steps, float32-ULP weight changes) and records
how the fit responds — continuously, or with a discrete jump when a
decision boundary (worst-outlier ``argmax`` selection, or the
``max|w| <= crit_value`` stopping test) is crossed.

Design decisions (VECR review, Rusudan + Karasunoendou):

* The fit under test is the **upstream implementation imported from
  isce3** — never a reimplementation. Where per-iteration internals
  are needed (stop margins, selection margins, removal order), a
  *traced mirror* of the upstream loop is used, and an equivalence
  test asserts the mirror reproduces upstream ``coefL`` / ``coefP`` /
  ``removed_indices`` bit-for-bit (see tests).
* Baselines match production: full-radar-grid normalization bounds,
  the measured NISAR ASC 139/019 sensor parameters for the w-test
  priors, float32 storage of offsets and correlation peaks upcast to
  float64 in the data matrix (the dtype path of rubbersheet.py).
* The primary judgment variable is the **coefficient difference**;
  offset-difference statistics over the scene are derived from it via
  the upstream ``predict_offsets``.

Scenarios:

* ``pure`` — self-contained synthetic perturbation sweeps;
* ``replay`` — the real-40k-sample layer: rebuild the exact production
  sample set from the on-disk dense-offsets rasters of the gpu3 / duel
  runs and replay the fit;
* ``minrepro`` — the public minimal synthetic reproducer (L0): a
  two-population 30x30 case, calibrated so that a single one-quantum
  (-1/32 px) flip of one high-weight sample at the real driver-node
  position forks the endgame membership and jumps the fitted surface
  by the target-class ~1e-2 px, exactly as observed on the real data;
* ``probe`` — the replay-based follow-up probes (transplants, delta
  and crit_value sweeps at the driver row) as a subcommand;
* ``membership`` — aggregate endgame-membership summary (final
  inlier counts, common inliers, the driver's removal iteration)
  derived from the removal sequences recorded in a ``replay`` npz;
  no fits are run and no sample data is emitted.

CLI usage::

    python scripts/polyfit_sensitivity.py pure [--json out.json]
        [--n-az 30] [--n-rg 30] [--noise-std 0.03] [--seed 0]
    python scripts/polyfit_sensitivity.py replay [--json out.json]
        [--npz out.npz] [--gate-only] [...path overrides]
    python scripts/polyfit_sensitivity.py minrepro [--json out.json]
        [--seed 29] [--hunt N] [--jump-min 0.01]
    python scripts/polyfit_sensitivity.py probe [--json out.json]
        [--row 22961] [--peak-only] [--pair] [--complement]
        [--deltas -1/32,-1e-4] [--quanta -2,1,2] [--crits 0.2,0.5]
    python scripts/polyfit_sensitivity.py membership --npz replay.npz
        [--driver-id 22961] [--json out.json]
"""

import argparse
import importlib.util
import json
import os
import pathlib
import sys

import numpy as np

# Production parameters of the NISAR ASC 139/019 fit — the exact
# values the workflow passes to polyfit_offsets, as rebuilt from the
# RIFG/RSLC files by build_replay_coordinates (abw =
# processedAzimuthBandwidth; rsr = c / (2 * slantRangeSpacing),
# exactly 48 MHz). They give sigmaL = 0.12470527678472171 px and
# sigmaP = 0.08333333333333334 px. (The earlier Phase-1 constants
# abw 1263.6805555555557 / rsr 47998723.51694915 were near-production
# approximations from other metadata and are retired.)
PROD_SENSOR = {"prf": 1520.0, "abw": 1263.68013808518,
               "rsr": 48000000.0, "rbw": 40000000.0}
PROD_GRID_SHAPE = (41040, 52906)
PROD_DEGREE = 2
PROD_CRIT_VALUE = 0.1
OFFSET_QUANTUM = 1.0 / 32.0  # Ampcor correlation-surface grid [px]

# isce3-benchmark sits inside an isce3 checkout; the fallback import
# path is the surrounding source tree (scripts/ -> isce3-benchmark/
# -> isce3/).
DEFAULT_ISCE3_PY = str(pathlib.Path(__file__).resolve().parents[2]
                       / "python/packages")


def load_offsets_polyfit():
    """Import the upstream isce3 offsets_polyfit module.

    Tries the installed ``isce3`` package first (container), then a
    direct file import from the isce3 source tree (host), pointed to
    by ``ISCE3_PY_SRC`` or the default checkout path. The direct
    import loads the identical upstream source file — the module under
    test is never reimplemented.

    Returns:
        module: The upstream ``offsets_polyfit`` module.
    """
    try:
        from isce3.math import offsets_polyfit
        return offsets_polyfit
    except ImportError:
        pass
    root = pathlib.Path(os.environ.get("ISCE3_PY_SRC", DEFAULT_ISCE3_PY))
    path = root / "isce3/math/offsets_polyfit.py"
    if not path.exists():
        raise ImportError(
            f"offsets_polyfit not importable and {path} not found; "
            "set ISCE3_PY_SRC to <isce3-src>/python/packages")
    spec = importlib.util.spec_from_file_location("offsets_polyfit",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_fit_kwargs(grid_shape=PROD_GRID_SHAPE):
    """The polyfit_offsets kwargs rubbersheet.py uses in production.

    Args:
        grid_shape: (length, width) of the full radar grid, used as
            the normalization bounds (NOT the sample extents).

    Returns:
        dict: Keyword arguments for ``polyfit_offsets``.
    """
    return {
        "degree": PROD_DEGREE,
        "crit_value": PROD_CRIT_VALUE,
        "minL": 0, "maxL": grid_shape[0],
        "minP": 0, "maxP": grid_shape[1],
        "prf": PROD_SENSOR["prf"], "abw": PROD_SENSOR["abw"],
        "rsr": PROD_SENSOR["rsr"], "rbw": PROD_SENSOR["rbw"],
    }


def make_pure_synthetic(n_az=30, n_rg=30, grid_shape=PROD_GRID_SHAPE,
                        coef_l=(0.4, 1.2, -0.6, 0.3, -0.2, 0.15),
                        coef_p=(-0.2, 0.5, 0.8, -0.25, 0.1, -0.3),
                        noise_std=0.03, peak_range=(0.2, 0.9),
                        quantize=True, seed=0):
    """Build a synthetic polyfit input in the production data layout.

    The truth is an exact degree-2 surface per band; Gaussian noise
    models the inlier scatter and is optionally quantized to the
    Ampcor 1/32 px correlation-surface grid. Offsets and peaks are
    stored as float32 first (the raw-raster dtype) and upcast on
    assembly, matching the rubbersheet.py cast path.

    Args:
        n_az: Sample-grid rows (production uses 200).
        n_rg: Sample-grid columns (production uses 200).
        grid_shape: Full radar-grid shape for coordinates and bounds.
        coef_l: True degree-2 coefficients, line band [px].
        coef_p: True degree-2 coefficients, pixel band [px].
        noise_std: Inlier noise standard deviation [px].
        peak_range: Uniform correlation-peak (fit weight) range.
        quantize: Snap the offsets to the 1/32 px grid.
        seed: RNG seed.

    Returns:
        tuple: (data (N, 6) float64 array, info dict).
    """
    rng = np.random.default_rng(seed)
    op = load_offsets_polyfit()
    lines = np.linspace(0, grid_shape[0] - 1, n_az).round()
    pixels = np.linspace(0, grid_shape[1] - 1, n_rg).round()
    ll, pp = np.meshgrid(lines, pixels, indexing="ij")
    ll, pp = ll.ravel(), pp.ravel()

    design = op.build_design_matrix(ll, pp, PROD_DEGREE,
                                    0, grid_shape[0], 0, grid_shape[1])
    d_l = design @ np.asarray(coef_l, dtype=float)
    d_p = design @ np.asarray(coef_p, dtype=float)
    d_l = d_l + rng.normal(0.0, noise_std, d_l.shape)
    d_p = d_p + rng.normal(0.0, noise_std, d_p.shape)
    if quantize:
        d_l = np.round(d_l / OFFSET_QUANTUM) * OFFSET_QUANTUM
        d_p = np.round(d_p / OFFSET_QUANTUM) * OFFSET_QUANTUM
    peak = rng.uniform(*peak_range, d_l.shape)

    # Raw rasters are float32; rubbersheet upcasts on column_stack.
    d_l = d_l.astype(np.float32).astype(np.float64)
    d_p = d_p.astype(np.float32).astype(np.float64)
    peak = peak.astype(np.float32).astype(np.float64)

    data = np.column_stack([np.arange(d_l.size, dtype=float),
                            ll, pp, d_l, d_p, peak])
    info = {"n_az": n_az, "n_rg": n_rg, "noise_std": noise_std,
            "quantize": quantize, "seed": seed,
            "coef_l_true": list(coef_l), "coef_p_true": list(coef_p)}
    return data, info


def polyfit_traced(op, data, degree=PROD_DEGREE,
                   crit_value=PROD_CRIT_VALUE, max_iterations=None,
                   minL=None, maxL=None, minP=None, maxP=None,
                   prf=None, abw=None, rsr=None, rbw=None,
                   coef_log=None, progress_label=None):
    """Traced mirror of the upstream ``polyfit_offsets`` loop.

    Replicates the upstream operation order exactly (see the
    equivalence test) while recording, per iteration, the two decision
    margins whose sign changes are the discrete switches of the fit:

    * stop margin: ``max(|wL|max, |wP|max) - crit_value`` — the
      stopping test (<= 0 terminates);
    * selection margin: the gap between the worst and second-worst
      combined outlier scores ``wL^2 + wP^2`` — a near-zero gap means
      an epsilon input change can swap the removal order.

    Args:
        op: The upstream offsets_polyfit module (basis functions are
            taken from it, not reimplemented).
        data: (N, 6) input array (copied internally).
        degree, crit_value, max_iterations, minL, maxL, minP, maxP,
        prf, abw, rsr, rbw: As in upstream ``polyfit_offsets``.
        coef_log: Optional list; when given, the per-iteration
            ``(coefL, coefP)`` pair (copies) is appended each
            iteration, including the final one — the coefficient
            trajectory of the removal chain.
        progress_label: Optional label; when given, a progress line
            is printed to stderr every 5000 iterations.

    Returns:
        tuple: (upstream-shaped result dict, trace list of dicts).
    """
    data = np.asarray(data, dtype=float).copy()
    if max_iterations is None:
        max_iterations = len(data)

    # Identical prior-sigma and bounds logic to upstream.
    sigmaL = 0.15 / ((prf / abw) if (None not in [prf, abw]) else 1.1)
    sigmaP = 0.10 / ((rsr / rbw) if (None not in [rsr, rbw]) else 1.1)
    maxL = data[:, 1].max() if maxL is None else maxL
    minL = data[:, 1].min() if minL is None else minL
    maxP = data[:, 2].max() if maxP is None else maxP
    minP = data[:, 2].min() if minP is None else minP

    nunk = op.ncoeffs(degree)
    eps = np.sqrt(np.finfo(float).eps)
    removed_indices = []
    trace = []

    A = op.build_design_matrix(data[:, 1], data[:, 2], degree,
                               minL, maxL, minP, maxP)

    import itertools
    for iteration in itertools.count():
        yL, yP = data[:, 3:4], data[:, 4:5]
        nobs = data.shape[0]
        if nobs <= nunk:
            raise ValueError(
                "No sufficient points for the rubbersheet polyfitting")

        w = np.clip(data[:, 5], eps, 1.0)
        Qy_diag = 1.0 / (w * w)
        W = w[:, None]
        A_til = A * W
        At = A_til.T
        Nmat = At @ A_til
        rhsL, rhsP = At @ (yL * W), At @ (yP * W)
        try:
            xL, Lc = op.cholesky_solve(Nmat, rhsL)
            xP, _ = op.cholesky_solve(Nmat, rhsP)
        except np.linalg.LinAlgError:
            I = np.eye(Nmat.shape[0])
            xL, Lc = op.cholesky_solve(Nmat + eps * I, rhsL)
            xP, _ = op.cholesky_solve(Nmat + eps * I, rhsP)
        Qx_hat = op.invert_from_cholesky(Lc)

        eL, eP = yL - A @ xL, yP - A @ xP
        Qyhat_diag = np.einsum("ij,jk,ik->i", A, Qx_hat, A)
        diag_Qe = np.clip(Qy_diag - Qyhat_diag, eps, None)
        s = np.sqrt(diag_Qe)
        wL, wP = eL[:, 0] / (s * sigmaL), eP[:, 0] / (s * sigmaP)
        max_any = max(np.abs(wL).max(), np.abs(wP).max())
        if coef_log is not None:
            coef_log.append((xL[:, 0].copy(), xP[:, 0].copy()))
        if progress_label is not None and iteration % 5000 == 0:
            log(f"{progress_label}: iteration {iteration}, "
                f"n_obs {nobs}")

        combined = wL * wL + wP * wP
        # argmax + masked max instead of a full argsort: identical
        # top-1 (the upstream removal choice) and margin, O(N).
        top1 = int(np.argmax(combined))
        if combined.size > 1:
            masked = combined.copy()
            masked[top1] = -np.inf
            top2 = int(np.argmax(masked))
        else:
            top2 = top1
        stop_component = ("L" if np.abs(wL).max() >= np.abs(wP).max()
                          else "P")
        record = {
            "iteration": iteration,
            "n_obs": int(nobs),
            "stop_margin": float(max_any - crit_value),
            "stop_component": stop_component,
            "stop_id": int(data[int(np.argmax(
                np.abs(wL if stop_component == "L" else wP))), 0]),
            "selection_margin": float(combined[top1] - combined[top2]),
            "top1_id": int(data[top1, 0]),
            "top2_id": int(data[top2, 0]),
        }
        trace.append(record)

        if (max_any <= crit_value) or (iteration >= max_iterations):
            return ({"coefL": xL[:, 0], "coefP": xP[:, 0],
                     "inliers": data,
                     "removed_indices": removed_indices,
                     "degree": degree, "design_nunk": nunk}, trace)

        worst = int(np.argmax(combined))
        record["removed_id"] = int(data[worst, 0])
        removed_indices.append(int(data[worst, 0]))
        data = np.delete(data, worst, axis=0)
        A = np.delete(A, worst, axis=0)


def first_divergence(removed_a, removed_b):
    """Index of the first difference between two removal sequences.

    Returns None when one sequence is a prefix of the other and both
    have equal length (i.e. identical sequences).
    """
    for i, (a, b) in enumerate(zip(removed_a, removed_b)):
        if a != b:
            return i
    if len(removed_a) != len(removed_b):
        return min(len(removed_a), len(removed_b))
    return None


def coef_delta_stats(op, dcoef_l, dcoef_p, grid_shape=PROD_GRID_SHAPE,
                     n_eval=101):
    """Scene-wide offset statistics of a coefficient difference.

    The coefficient difference is itself a degree-2 surface, so the
    induced offset difference is evaluated exactly from it via the
    upstream ``predict_offsets`` on an evaluation grid.

    Args:
        op: The upstream offsets_polyfit module.
        dcoef_l: coefL difference vector.
        dcoef_p: coefP difference vector.
        grid_shape: Full radar-grid shape.
        n_eval: Evaluation-grid points per axis.

    Returns:
        dict: Per-band signed mean/median, RMS, p99, max |diff| and
        peak-to-peak of the induced offset difference [px].
    """
    lines = np.linspace(0, grid_shape[0], n_eval)
    pixels = np.linspace(0, grid_shape[1], n_eval)
    ll, pp = np.meshgrid(lines, pixels, indexing="ij")
    d_l, d_p = op.predict_offsets(ll, pp, dcoef_l, dcoef_p,
                                  PROD_DEGREE, 0, grid_shape[0],
                                  0, grid_shape[1])
    out = {}
    for band, d in (("l", d_l), ("p", d_p)):
        v = d.ravel()
        out[band] = {
            "mean": float(v.mean()),
            "median": float(np.median(v)),
            "rms": float(np.sqrt(np.mean(v ** 2))),
            "p99_abs": float(np.percentile(np.abs(v), 99)),
            "max_abs": float(np.abs(v).max()),
            "peak_to_peak": float(v.max() - v.min()),
        }
    return out


def sweep_positions(n_az, n_rg):
    """Center / edge / corner sample indices of an (n_az, n_rg) grid."""
    return {
        "center": (n_az // 2) * n_rg + n_rg // 2,
        "edge": (n_az // 2) * n_rg,
        "corner": 0,
    }


def run_single_point_sweep(op, base_data, fit_kwargs, position_name,
                           position_idx, deltas, band="dL"):
    """Sweep a single-sample offset perturbation and record the fit.

    Args:
        op: The upstream offsets_polyfit module.
        base_data: Baseline (N, 6) data array.
        fit_kwargs: Kwargs for the fit (production_fit_kwargs()).
        position_name: Label of the perturbed sample position.
        position_idx: Row index of the perturbed sample.
        deltas: Iterable of perturbation amplitudes [px].
        band: "dL" (column 3) or "dP" (column 4).

    Returns:
        dict: Baseline summary and per-delta records.
    """
    col = {"dL": 3, "dP": 4}[band]
    base_res, base_trace = polyfit_traced(op, base_data, **fit_kwargs)
    baseline = {
        "n_removed": len(base_res["removed_indices"]),
        "n_iterations": len(base_trace),
        "min_selection_margin": min(t["selection_margin"]
                                    for t in base_trace),
        "final_stop_margin": base_trace[-1]["stop_margin"],
    }
    records = []
    for delta in deltas:
        data = base_data.copy()
        data[position_idx, col] += delta
        try:
            res, trace = polyfit_traced(op, data, **fit_kwargs)
        except ValueError as exc:
            records.append({"delta": float(delta),
                            "error": str(exc)})
            continue
        stats = coef_delta_stats(
            op, res["coefL"] - base_res["coefL"],
            res["coefP"] - base_res["coefP"])
        records.append({
            "delta": float(delta),
            "n_removed": len(res["removed_indices"]),
            "first_divergence": first_divergence(
                base_res["removed_indices"], res["removed_indices"]),
            "perturbed_id_removed": int(base_data[position_idx, 0])
            in res["removed_indices"],
            "dcoefL": [float(c) for c in
                       res["coefL"] - base_res["coefL"]],
            "dcoefP": [float(c) for c in
                       res["coefP"] - base_res["coefP"]],
            "induced_offset": stats,
            "min_selection_margin": min(t["selection_margin"]
                                        for t in trace),
        })
    return {"position": position_name, "band": band,
            "baseline": baseline, "records": records}


def run_pure_scenario(args):
    """Run the pure-synthetic scenario (baseline + A/A + sweeps)."""
    op = load_offsets_polyfit()
    data, info = make_pure_synthetic(
        n_az=args.n_az, n_rg=args.n_rg, noise_std=args.noise_std,
        seed=args.seed)
    fit_kwargs = production_fit_kwargs()

    # A/A determinism control: two runs on the identical input must
    # agree bit-for-bit (upstream implementation, not the mirror).
    res1 = op.polyfit_offsets(data.copy(), max_iterations=len(data),
                              **fit_kwargs)
    res2 = op.polyfit_offsets(data.copy(), max_iterations=len(data),
                              **fit_kwargs)
    aa_identical = (np.array_equal(res1["coefL"], res2["coefL"])
                    and np.array_equal(res1["coefP"], res2["coefP"])
                    and res1["removed_indices"]
                    == res2["removed_indices"])

    # Mirror equivalence on this dataset (the tests assert it too;
    # recorded here so every result JSON carries its own proof).
    mres, _ = polyfit_traced(op, data, **fit_kwargs)
    mirror_equivalent = (
        np.array_equal(res1["coefL"], mres["coefL"])
        and np.array_equal(res1["coefP"], mres["coefP"])
        and res1["removed_indices"] == mres["removed_indices"])

    deltas = sorted(set(
        [OFFSET_QUANTUM * k for k in (1, 2, 4, 8, 16, 32)]
        + list(np.logspace(-3, np.log10(60.0), args.n_deltas))))
    sweeps = []
    for name, idx in sweep_positions(args.n_az, args.n_rg).items():
        for band in ("dL", "dP"):
            sweeps.append(run_single_point_sweep(
                op, data, fit_kwargs, name, idx, deltas, band))

    return {
        "scenario": "pure",
        "synthetic": info,
        "fit_kwargs": {k: (v if not isinstance(v, float) else float(v))
                       for k, v in fit_kwargs.items()},
        "sigmaL": 0.15 / (PROD_SENSOR["prf"] / PROD_SENSOR["abw"]),
        "sigmaP": 0.10 / (PROD_SENSOR["rsr"] / PROD_SENSOR["rbw"]),
        "aa_identical": aa_identical,
        "mirror_equivalent": mirror_equivalent,
        "n_samples": int(len(data)),
        "baseline_n_removed": len(res1["removed_indices"]),
        "sweeps": sweeps,
    }


# ---------------------------------------------------------------------
# replay scenario: the real-40k-sample layer (Phase 2)
# ---------------------------------------------------------------------

SPEED_OF_LIGHT = 299792458.0
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRATCH = pathlib.Path.home() / "scratch"
REPLAY_DEFAULTS = {
    "rifg": _SCRATCH / "gunw_ASC139_019_gpu3/RIFG.h5",
    "ref_rslc": _REPO_ROOT / ("data/RSLC/NISAR_L1_PR_RSLC_024_139_A_"
                              "019_4005_DHDH_A_20260705T195626_"
                              "20260705T195652_P05023_N_P_J_001.h5"),
    "gpu_dir": _SCRATCH / "gunw_ASC139_019_gpu3/dense_offsets/freqA/HH",
    "cpu_a_dir": _SCRATCH / "ampcor_duel_cpu/dense_offsets/freqA/HH",
    "cpu_b_dir": _SCRATCH / "ampcor_duelB_cpu/dense_offsets/freqA/HH",
    "culled_dir": _SCRATCH / ("gunw_ASC139_019_gpu3/rubbersheet_offsets"
                              "/freqA/HH"),
    "gpu_log": _SCRATCH / "gunw_gpu_run3_out/insar.log",
    "cpu_log": _SCRATCH / "gunw_cpu_run2_out/insar.log",
}


def open_envi_band(path, band=1):
    """Read one band of a flat-binary ENVI raster into memory.

    Args:
        path: Path of the binary file (``.hdr`` sidecar required).
        band: One-based band index.

    Returns:
        numpy.ndarray: (lines, samples) array in the native dtype.
    """
    from compare_slc import ENVI_DTYPES, read_envi_header
    path = pathlib.Path(path)
    fields = read_envi_header(path)
    lines = int(fields["lines"])
    samples = int(fields["samples"])
    bands = int(fields.get("bands", 1))
    dtype = np.dtype(ENVI_DTYPES[int(fields["data type"])])
    if int(fields.get("byte order", 0)) != 0:
        dtype = dtype.newbyteorder(">")
    interleave = fields.get("interleave", "bip").lower()
    mm = np.memmap(path, dtype=dtype, mode="r")
    if interleave == "bip":
        arr = mm.reshape(lines, samples, bands)[:, :, band - 1]
    elif interleave == "bsq":
        arr = mm.reshape(bands, lines, samples)[band - 1]
    elif interleave == "bil":
        arr = mm.reshape(lines, bands, samples)[:, band - 1, :]
    else:
        raise ValueError(f"{path}: unsupported interleave "
                         f"{interleave!r}")
    return np.array(arr)


def file_md5(path, block=1 << 22):
    """Streaming md5 hex digest of a file."""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def build_replay_coordinates(rifg_path, ref_rslc_path,
                             n_az=200, n_rg=200):
    """Rebuild the production polyfit sample coordinates (freqA).

    Replicates ``run_rubbersheet_with_polyfit`` exactly: the offsets
    grid line/pixel indices come from the RIFG ``pixelOffsets``
    ``zeroDopplerTime`` / ``slantRange`` datasets converted through the
    reference-RSLC epoch, PRF and slant-range spacing, and the sample
    subset is ``np.linspace(0, len-1, n, dtype=int)`` on each axis.
    All sensor parameters (prf/abw/rsr/rbw) and the normalization
    bounds are read from the files, not hardcoded.

    Args:
        rifg_path: RIFG HDF5 with the pixelOffsets grid datasets.
        ref_rslc_path: Reference RSLC HDF5.
        n_az: Samples along azimuth (production: 200).
        n_rg: Samples along range (production: 200).

    Returns:
        dict: Coordinate arrays, fit kwargs and provenance scalars.
    """
    import h5py
    with h5py.File(ref_rslc_path, "r") as f:
        sw = f["science/LSAR/RSLC/swaths"]
        az_t0 = float(sw["zeroDopplerTime"][0])
        n_az_times = int(sw["zeroDopplerTime"].shape[0])
        prf = 1.0 / float(sw["zeroDopplerTimeSpacing"][()])
        sr0 = float(sw["frequencyA/slantRange"][0])
        n_slant_ranges = int(sw["frequencyA/slantRange"].shape[0])
        rg_spacing = float(sw["frequencyA/slantRangeSpacing"][()])
        abw = float(sw["frequencyA/processedAzimuthBandwidth"][()])
        rbw = float(sw["frequencyA/processedRangeBandwidth"][()])
    with h5py.File(rifg_path, "r") as f:
        po = f["science/LSAR/RIFG/swaths/frequencyA/pixelOffsets"]
        off_zdt = po["zeroDopplerTime"][()]
        off_sr = po["slantRange"][()]

    off_az_indices = ((off_zdt - az_t0) * prf).round().astype(int)
    off_rg_indices = ((off_sr - sr0) / rg_spacing).round().astype(int)
    n_az = min(n_az, len(off_az_indices))
    n_rg = min(n_rg, len(off_rg_indices))
    az_sampled = np.linspace(0, len(off_az_indices) - 1, n_az,
                             dtype=int)
    rg_sampled = np.linspace(0, len(off_rg_indices) - 1, n_rg,
                             dtype=int)
    fit_kwargs = {
        "degree": PROD_DEGREE, "crit_value": PROD_CRIT_VALUE,
        "minL": 0, "maxL": n_az_times,
        "minP": 0, "maxP": n_slant_ranges,
        "prf": prf, "abw": abw,
        "rsr": SPEED_OF_LIGHT / rg_spacing * 0.5, "rbw": rbw,
    }
    return {
        "off_az_indices": off_az_indices,
        "off_rg_indices": off_rg_indices,
        "az_sampled": az_sampled, "rg_sampled": rg_sampled,
        "az_polyfit_indices": off_az_indices[az_sampled],
        "rg_polyfit_indices": off_rg_indices[rg_sampled],
        "fit_kwargs": fit_kwargs,
        "provenance": {
            "az_t0": az_t0, "prf": prf, "sr0": sr0,
            "rg_spacing": rg_spacing, "abw": abw, "rbw": rbw,
            "n_az_times": n_az_times,
            "n_slant_ranges": n_slant_ranges,
            "sigmaL": 0.15 / (prf / abw),
            "sigmaP": 0.10 / ((SPEED_OF_LIGHT / rg_spacing * 0.5)
                              / rbw),
        },
    }


def extract_replay_samples(offsets_dir, coords):
    """Extract the production 40k sample matrix from a raw-offsets dir.

    Mirrors the rubbersheet.py data assembly: float32 rasters are
    indexed on the sample grid and upcast to float64 by
    ``np.column_stack``; the row order is azimuth-major.

    Args:
        offsets_dir: Directory holding ``dense_offsets`` (2-band BIP:
            az, rg) and ``correlation_peak`` ENVI rasters.
        coords: Output of :func:`build_replay_coordinates`.

    Returns:
        numpy.ndarray: (N, 6) ``[id, line, pixel, dL, dP, corr_peak]``.
    """
    offsets_dir = pathlib.Path(offsets_dir)
    az_off = open_envi_band(offsets_dir / "dense_offsets", 1)
    rg_off = open_envi_band(offsets_dir / "dense_offsets", 2)
    peak = open_envi_band(offsets_dir / "correlation_peak", 1)
    lines, pixels = np.meshgrid(coords["az_sampled"],
                                coords["rg_sampled"], indexing="ij")
    az_pf = coords["az_polyfit_indices"]
    rg_pf = coords["rg_polyfit_indices"]
    return np.column_stack([
        np.arange(lines.size),
        np.repeat(az_pf, len(rg_pf)),
        np.tile(rg_pf, len(az_pf)),
        az_off[lines, pixels].ravel(),
        rg_off[lines, pixels].ravel(),
        peak[lines, pixels].ravel(),
    ])


def parse_logged_coefs(log_path):
    """Parse the polyfit coefficients printed to an insar.log.

    ``run_rubbersheet_with_polyfit`` prints ``Polyfitting CoefL:
    [ ... ]`` (numpy array formatting, possibly line-wrapped) once per
    processed polyfit; the first occurrence (freqA) is returned.

    Args:
        log_path: Path of the insar.log.

    Returns:
        dict: {"coefL": [6 floats], "coefP": [6 floats]}.
    """
    import re
    text = pathlib.Path(log_path).read_text(errors="replace")
    out = {}
    for key in ("CoefL", "CoefP"):
        m = re.search(rf"Polyfitting {key}:\s*\[([^\]]*)\]", text,
                      re.S)
        if m is None:
            raise ValueError(f"{log_path}: no 'Polyfitting {key}'")
        out["coef" + key[-1]] = [float(x) for x in m.group(1).split()]
    return out


def make_factorial_data(base, donor, channel):
    """Transplant one input channel of the fit from another sample set.

    Args:
        base: (N, 6) baseline sample matrix.
        donor: (N, 6) donor sample matrix (same coordinates).
        channel: "offsets" (columns dL, dP) or "weights" (corr_peak).

    Returns:
        numpy.ndarray: Copy of ``base`` with the channel replaced.
    """
    cols = {"offsets": [3, 4], "weights": [5]}[channel]
    data = base.copy()
    data[:, cols] = donor[:, cols]
    return data


def coef_vector_judgment(dcoef_l, dcoef_p, target_l, target_p):
    """Judge a replayed coefficient delta against the target vector.

    Args:
        dcoef_l, dcoef_p: Replayed coefficient deltas (6 each).
        target_l, target_p: Target coefficient deltas (6 each).

    Returns:
        dict: Norm, norm ratio and cosine similarity, overall and per
        band ("all" = 12-dim concatenation).
    """
    out = {}
    pairs = {
        "all": (np.concatenate([dcoef_l, dcoef_p]),
                np.concatenate([target_l, target_p])),
        "L": (np.asarray(dcoef_l), np.asarray(target_l)),
        "P": (np.asarray(dcoef_p), np.asarray(target_p)),
    }
    for name, (v, t) in pairs.items():
        nv, nt = float(np.linalg.norm(v)), float(np.linalg.norm(t))
        out[name] = {
            "norm": nv, "target_norm": nt,
            "norm_ratio": nv / nt if nt else None,
            "cosine": (float(v @ t / (nv * nt))
                       if nv > 0 and nt > 0 else None),
        }
    return out


def induced_rms_curves(op, coef_log_a, coef_log_b, fit_kwargs,
                       n_eval=41):
    """Per-iteration induced-field RMS between two coef trajectories.

    Args:
        op: The upstream offsets_polyfit module.
        coef_log_a, coef_log_b: Lists of (coefL, coefP) per iteration.
        fit_kwargs: Fit kwargs carrying the normalization bounds.
        n_eval: Evaluation-grid points per axis.

    Returns:
        dict: {"rms_l": (n,), "rms_p": (n,)} arrays [px], where
        n = min(len(a), len(b)) aligned by iteration index.
    """
    n = min(len(coef_log_a), len(coef_log_b))
    lines = np.linspace(fit_kwargs["minL"], fit_kwargs["maxL"], n_eval)
    pixels = np.linspace(fit_kwargs["minP"], fit_kwargs["maxP"],
                         n_eval)
    ll, pp = np.meshgrid(lines, pixels, indexing="ij")
    a_eval = op.build_design_matrix(
        ll.ravel(), pp.ravel(), PROD_DEGREE,
        fit_kwargs["minL"], fit_kwargs["maxL"],
        fit_kwargs["minP"], fit_kwargs["maxP"])
    d_l = np.stack([coef_log_a[i][0] - coef_log_b[i][0]
                    for i in range(n)])
    d_p = np.stack([coef_log_a[i][1] - coef_log_b[i][1]
                    for i in range(n)])
    rms_l = np.sqrt(np.mean((a_eval @ d_l.T) ** 2, axis=0))
    rms_p = np.sqrt(np.mean((a_eval @ d_p.T) ** 2, axis=0))
    return {"rms_l": rms_l, "rms_p": rms_p}


def sample_id_for_node(coords, raw_line, raw_sample):
    """Sample-row id of a raw offsets-grid node, or None if unsampled.

    Args:
        coords: Output of :func:`build_replay_coordinates`.
        raw_line: Offsets-grid line index (0-based).
        raw_sample: Offsets-grid sample index (0-based).

    Returns:
        int | None: Row id in the azimuth-major sample matrix.
    """
    az_pos = np.nonzero(coords["az_sampled"] == raw_line)[0]
    rg_pos = np.nonzero(coords["rg_sampled"] == raw_sample)[0]
    if az_pos.size and rg_pos.size:
        return int(az_pos[0] * len(coords["rg_sampled"]) + rg_pos[0])
    return None


def _margins_around(trace, iteration, halo=2):
    """Trace records within ``halo`` iterations of ``iteration``."""
    lo, hi = iteration - halo, iteration + halo
    return [{k: t[k] for k in
             ("iteration", "n_obs", "stop_margin", "selection_margin",
              "top1_id", "top2_id") if k in t}
            for t in trace if lo <= t["iteration"] <= hi]


def _fit_summary(res, trace, seconds):
    """Compact JSON summary of one traced fit."""
    return {
        "n_removed": len(res["removed_indices"]),
        "n_iterations": len(trace),
        "final_stop_margin": trace[-1]["stop_margin"],
        "min_selection_margin": min(t["selection_margin"]
                                    for t in trace),
        "coefL": [float(c) for c in res["coefL"]],
        "coefP": [float(c) for c in res["coefP"]],
        "seconds": round(seconds, 2),
    }


def run_replay_scenario(args):
    """Run the real-40k-sample replay (gate + legs + factorial +
    microscope)."""
    import time as _time
    op = load_offsets_polyfit()
    coords = build_replay_coordinates(args.rifg, args.ref_rslc)
    fit_kwargs = coords["fit_kwargs"]

    run_dirs = {"G": pathlib.Path(args.gpu_dir),
                "A": pathlib.Path(args.cpu_a_dir),
                "B": pathlib.Path(args.cpu_b_dir)}
    home = str(pathlib.Path.home())
    samples, inputs = {}, {}
    for key, d in run_dirs.items():
        samples[key] = extract_replay_samples(d, coords)
        inputs[key] = {
            # Home-relative so committed artifacts carry no username.
            "dir": str(d).replace(home, "~"),
            "dense_offsets_md5": file_md5(d / "dense_offsets"),
            "correlation_peak_md5": file_md5(d / "correlation_peak"),
        }

    result = {
        "scenario": "replay",
        "inputs": inputs,
        "n_samples": int(samples["G"].shape[0]),
        "fit_kwargs": {k: float(v) if isinstance(v, float) else v
                       for k, v in fit_kwargs.items()},
        "provenance": coords["provenance"],
        "blas_env": {k: os.environ.get(k) for k in
                     ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                      "MKL_NUM_THREADS")},
    }

    # --- self-consistency gate (G) --------------------------------
    log(f"gate: fitting G ({samples['G'].shape[0]} samples) ...")
    t0 = _time.time()
    up1 = op.polyfit_offsets(samples["G"].copy(),
                             max_iterations=len(samples["G"]),
                             **fit_kwargs)
    gate_seconds = _time.time() - t0
    up2 = op.polyfit_offsets(samples["G"].copy(),
                             max_iterations=len(samples["G"]),
                             **fit_kwargs)
    aa_identical = (np.array_equal(up1["coefL"], up2["coefL"])
                    and np.array_equal(up1["coefP"], up2["coefP"])
                    and up1["removed_indices"]
                    == up2["removed_indices"])

    ll, pp = np.meshgrid(coords["off_az_indices"],
                         coords["off_rg_indices"], indexing="ij")
    pred_az, pred_rg = op.predict_offsets(
        ll, pp, up1["coefL"], up1["coefP"], PROD_DEGREE,
        fit_kwargs["minL"], fit_kwargs["maxL"],
        fit_kwargs["minP"], fit_kwargs["maxP"])
    culled_dir = pathlib.Path(args.culled_dir)
    disk_az = open_envi_band(culled_dir / "culled_az_offsets")
    disk_rg = open_envi_band(culled_dir / "culled_rg_offsets")
    logged_gpu = parse_logged_coefs(args.gpu_log)
    logged_cpu = parse_logged_coefs(args.cpu_log)
    gate = {
        "aa_identical": aa_identical,
        "n_removed": len(up1["removed_indices"]),
        "upstream_fit_seconds": round(gate_seconds, 2),
        "culled_az_max_abs_diff": float(
            np.abs(pred_az - disk_az).max()),
        "culled_rg_max_abs_diff": float(
            np.abs(pred_rg - disk_rg).max()),
        "logged_gpu_coefL_max_abs_diff": float(np.abs(
            up1["coefL"] - np.array(logged_gpu["coefL"])).max()),
        "logged_gpu_coefP_max_abs_diff": float(np.abs(
            up1["coefP"] - np.array(logged_gpu["coefP"])).max()),
    }
    result["gate"] = gate
    result["logged"] = {"gpu": logged_gpu, "cpu": logged_cpu}
    log(f"gate: n_removed={gate['n_removed']} "
        f"culled max|d| az={gate['culled_az_max_abs_diff']:.3g} "
        f"rg={gate['culled_rg_max_abs_diff']:.3g} "
        f"logged max|d| L={gate['logged_gpu_coefL_max_abs_diff']:.3g} "
        f"({gate['upstream_fit_seconds']} s)")

    # Target: the observed CPU-minus-GPU coefficient difference,
    # taken from the production logs (exact to print precision).
    target_l = (np.array(logged_cpu["coefL"])
                - np.array(logged_gpu["coefL"]))
    target_p = (np.array(logged_cpu["coefP"])
                - np.array(logged_gpu["coefP"]))
    result["target"] = {"dcoefL_cpu_minus_gpu": target_l.tolist(),
                       "dcoefP_cpu_minus_gpu": target_p.tolist(),
                       "induced_offset": coef_delta_stats(
                           op, target_l, target_p)}
    if args.gate_only:
        return result, {}

    # --- traced fits with coefficient trajectories ----------------
    fits, traces, coef_logs = {}, {}, {}
    for key in ("G", "A", "B"):
        log(f"replay: traced fit {key} ...")
        clog = []
        t0 = _time.time()
        res, trace = polyfit_traced(op, samples[key], coef_log=clog,
                                    progress_label=f"traced {key}",
                                    **fit_kwargs)
        fits[key] = _fit_summary(res, trace, _time.time() - t0)
        fits[key]["removed_indices"] = res["removed_indices"]
        traces[key], coef_logs[key] = trace, clog
        log(f"replay: {key} n_removed={fits[key]['n_removed']} "
            f"({fits[key]['seconds']} s)")
    mirror_equivalent = (
        np.array_equal(up1["coefL"],
                       np.array(fits["G"]["coefL"]))
        and np.array_equal(up1["coefP"],
                           np.array(fits["G"]["coefP"]))
        and up1["removed_indices"] == fits["G"]["removed_indices"])
    result["gate"]["mirror_equivalent"] = mirror_equivalent

    # --- input-channel differences between the sample sets --------
    def channel_diff(a, b):
        off_rows = np.nonzero((a[:, 3] != b[:, 3])
                              | (a[:, 4] != b[:, 4]))[0]
        w_rows = np.nonzero(a[:, 5] != b[:, 5])[0]
        return off_rows, w_rows

    result["channel_diffs"] = {}
    for pair in (("A", "G"), ("B", "G"), ("B", "A")):
        off_rows, w_rows = channel_diff(samples[pair[0]],
                                        samples[pair[1]])
        result["channel_diffs"]["_vs_".join(pair)] = {
            "offset_rows": off_rows.tolist(),
            "n_offset_rows": int(off_rows.size),
            "n_weight_rows": int(w_rows.size),
            "max_abs_d_offset": (float(np.abs(
                samples[pair[0]][off_rows, 3:5]
                - samples[pair[1]][off_rows, 3:5]).max())
                if off_rows.size else 0.0),
            "max_abs_d_weight": (float(np.abs(
                samples[pair[0]][w_rows, 5]
                - samples[pair[1]][w_rows, 5]).max())
                if w_rows.size else 0.0),
        }

    # --- legs judged against the target ---------------------------
    def leg(name, coef_l, coef_p, base_l, base_p, extra=None):
        d_l = np.asarray(coef_l) - np.asarray(base_l)
        d_p = np.asarray(coef_p) - np.asarray(base_p)
        entry = {
            "dcoefL": d_l.tolist(), "dcoefP": d_p.tolist(),
            "judgment_vs_target": coef_vector_judgment(
                d_l, d_p, target_l, target_p),
            "induced_offset": coef_delta_stats(op, d_l, d_p),
            "residual_vs_target_induced": coef_delta_stats(
                op, d_l - target_l, d_p - target_p),
        }
        if extra:
            entry.update(extra)
        result["legs"][name] = entry

    result["legs"] = {}
    for key in ("A", "B"):
        fd = first_divergence(fits["G"]["removed_indices"],
                              fits[key]["removed_indices"])
        shared = (len(set(fits["G"]["removed_indices"])
                      & set(fits[key]["removed_indices"]))
                  / max(len(fits["G"]["removed_indices"]), 1))
        leg(f"{key}_vs_G", fits[key]["coefL"], fits[key]["coefP"],
            fits["G"]["coefL"], fits["G"]["coefP"],
            {"first_divergence": fd,
             "shared_removed_fraction": shared,
             "n_removed": fits[key]["n_removed"],
             "n_removed_base": fits["G"]["n_removed"]})
    leg("B_vs_A", fits["B"]["coefL"], fits["B"]["coefP"],
        fits["A"]["coefL"], fits["A"]["coefP"],
        {"first_divergence": first_divergence(
            fits["A"]["removed_indices"],
            fits["B"]["removed_indices"])})

    # --- 2x2 factorial: which channel moves the chain -------------
    result["factorial"] = {}
    for key in ("A", "B"):
        for channel in ("offsets", "weights"):
            name = f"{key}_{channel}_only_vs_G"
            log(f"factorial: {name} ...")
            data = make_factorial_data(samples["G"], samples[key],
                                       channel)
            t0 = _time.time()
            res, trace = polyfit_traced(op, data, **fit_kwargs)
            summary = _fit_summary(res, trace, _time.time() - t0)
            d_l = np.array(summary["coefL"]) - np.array(
                fits["G"]["coefL"])
            d_p = np.array(summary["coefP"]) - np.array(
                fits["G"]["coefP"])
            result["factorial"][name] = {
                "n_removed": summary["n_removed"],
                "seconds": summary["seconds"],
                "first_divergence": first_divergence(
                    fits["G"]["removed_indices"],
                    res["removed_indices"]),
                "dcoefL": d_l.tolist(), "dcoefP": d_p.tolist(),
                "judgment_vs_target": coef_vector_judgment(
                    d_l, d_p, target_l, target_p),
                "induced_offset": coef_delta_stats(op, d_l, d_p),
            }

    # --- microscope: the shared large flip node -------------------
    raw_line, raw_sample = (int(x) for x
                            in args.flip_node.split(","))
    flip_id = sample_id_for_node(coords, raw_line, raw_sample)
    microscope = {"flip_node": {"raw_line": raw_line,
                                "raw_sample": raw_sample,
                                "sample_id": flip_id}}
    if flip_id is not None:
        per_run = {}
        for key in ("G", "A", "B"):
            removed = fits[key]["removed_indices"]
            it = (removed.index(flip_id) if flip_id in removed
                  else None)
            row = samples[key][flip_id]
            per_run[key] = {
                "removal_iteration": it,
                "dL": float(row[3]), "dP": float(row[4]),
                "corr_peak": float(row[5]),
                "margins_around": (_margins_around(traces[key], it)
                                   if it is not None else None),
            }
        microscope["per_run"] = per_run
    for key in ("A", "B"):
        fd = result["legs"][f"{key}_vs_G"]["first_divergence"]
        if fd is not None:
            microscope[f"divergence_{key}_vs_G"] = {
                "iteration": fd,
                "G": _margins_around(traces["G"], fd, halo=1),
                key: _margins_around(traces[key], fd, halo=1),
            }
    result["microscope"] = microscope

    # --- per-node hybrids: minimal destructive subset probes ------
    hybrids = []
    probed = set()
    for key in ("A", "B"):
        rows = result["channel_diffs"][f"{key}_vs_G"]["offset_rows"]
        for r in rows:
            if len(hybrids) >= args.max_hybrids:
                break
            # Identical transplants from the two donors are
            # redundant (the shared flip windows carry the same
            # CPU-side peak choice in A and B).
            transplant = (int(r), float(samples[key][r, 3]),
                          float(samples[key][r, 4]))
            if transplant in probed:
                continue
            probed.add(transplant)
            log(f"hybrid: {key} row {r} ...")
            data = samples["G"].copy()
            data[r, 3:5] = samples[key][r, 3:5]
            res = op.polyfit_offsets(data, max_iterations=len(data),
                                     **fit_kwargs)
            d_l = res["coefL"] - np.array(fits["G"]["coefL"])
            d_p = res["coefP"] - np.array(fits["G"]["coefP"])
            hybrids.append({
                "donor": key, "row": int(r),
                "line": float(samples["G"][r, 1]),
                "pixel": float(samples["G"][r, 2]),
                "d_offset": [float(samples[key][r, 3]
                                   - samples["G"][r, 3]),
                             float(samples[key][r, 4]
                                   - samples["G"][r, 4])],
                "first_divergence": first_divergence(
                    fits["G"]["removed_indices"],
                    res["removed_indices"]),
                "n_removed": len(res["removed_indices"]),
                "judgment_vs_target": coef_vector_judgment(
                    d_l, d_p, target_l, target_p),
                "induced_offset_rms_l": coef_delta_stats(
                    op, d_l, d_p)["l"]["rms"],
            })
    result["hybrids"] = hybrids

    # --- arrays for the npz ---------------------------------------
    arrays = {}
    for key in ("G", "A", "B"):
        arrays[f"samples_{key}"] = samples[key]
        arrays[f"removed_{key}"] = np.array(
            fits[key]["removed_indices"], dtype=np.int64)
        arrays[f"stop_margin_{key}"] = np.array(
            [t["stop_margin"] for t in traces[key]])
        arrays[f"selection_margin_{key}"] = np.array(
            [t["selection_margin"] for t in traces[key]])
        arrays[f"coef_log_L_{key}"] = np.stack(
            [c[0] for c in coef_logs[key]])
        arrays[f"coef_log_P_{key}"] = np.stack(
            [c[1] for c in coef_logs[key]])
    for key in ("A", "B"):
        curves = induced_rms_curves(op, coef_logs[key],
                                    coef_logs["G"], fit_kwargs)
        arrays[f"amp_curve_rms_l_{key}_vs_G"] = curves["rms_l"]
        arrays[f"amp_curve_rms_p_{key}_vs_G"] = curves["rms_p"]
        # summarize into JSON: start, end, max
        result["legs"][f"{key}_vs_G"]["amplification_curve"] = {
            "start_rms_l": float(curves["rms_l"][0]),
            "end_rms_l": float(curves["rms_l"][-1]),
            "max_rms_l": float(curves["rms_l"].max()),
            "start_rms_p": float(curves["rms_p"][0]),
            "end_rms_p": float(curves["rms_p"][-1]),
            "max_rms_p": float(curves["rms_p"].max()),
        }
    # Keep the removal sequences out of the JSON (they are in the
    # npz); leave only their lengths.
    for key in ("G", "A", "B"):
        fits[key].pop("removed_indices")
    result["fits"] = fits
    return result, arrays


# ---------------------------------------------------------------------
# minrepro scenario: the public minimal synthetic reproducer (L0)
# ---------------------------------------------------------------------

# The real driver node of the L1 replay: sample row 22961, radar
# coordinates (line 23405 of 41040, pixel 42589 of 52906), corr_peak
# 0.9485, flipped by exactly -1/32 px in azimuth between the CPU and
# GPU Ampcor runs.
MIN_REPRO_DRIVER_RADAR = (23405, 42589)
MIN_REPRO_DRIVER_PEAK = 0.9485
# Calibrated seed of the pinned existence proof (see run_min_repro
# hunting): baseline retention 3.6%, driver survives; the -1/32 px
# flip removes it at 95.6% of the removal chain and jumps the fitted
# azimuth surface by RMS 2.8e-2 px (real-data target: 3.6e-2 px).
MIN_REPRO_SEED = 29


def make_min_repro_case(seed=MIN_REPRO_SEED, n_az=30, n_rg=30,
                        grid_shape=PROD_GRID_SHAPE,
                        coef_l=(0.4, 1.2, -0.6, 0.3, -0.2, 0.15),
                        coef_p=(-0.2, 0.5, 0.8, -0.25, 0.1, -0.3),
                        coherent_fraction=0.08,
                        coherent_noise_std=0.02,
                        coherent_peak_range=(0.3, 0.8),
                        junk_scatter=20.0,
                        junk_peak_range=(0.02, 0.15),
                        quant_phase=0.0,
                        driver_radar=None):
    """Build the two-population minimal-reproducer sample set.

    The structure imitates the real production sample set at 900
    samples: a small *coherent* population (high correlation peaks,
    small Gaussian scatter around a smooth degree-2 truth surface)
    embedded in a *junk* majority (low peaks, offsets scattered over
    the Ampcor search-window scale) — the composition that makes the
    production fit remove ~96% of the samples and settle on a small
    high-weight elite. One coherent sample, the *driver*, is placed at
    the grid node nearest to the real driver's radar position, gets
    the real driver's correlation peak, and measures the truth surface
    exactly (its residual is pure 1/32 px quantization error). All
    offsets are quantized to the Ampcor 1/32 px grid and stored
    through the production float32 path.

    Args:
        seed: RNG seed (the default is the pinned existence proof).
        n_az: Sample-grid rows.
        n_rg: Sample-grid columns.
        grid_shape: Full radar-grid shape for coordinates and bounds.
        coef_l: True degree-2 coefficients, line band [px].
        coef_p: True degree-2 coefficients, pixel band [px].
        coherent_fraction: Fraction of coherent samples.
        coherent_noise_std: Coherent-population scatter [px].
        coherent_peak_range: Coherent correlation-peak range.
        junk_scatter: Junk-population uniform scatter half-width [px].
        junk_peak_range: Junk correlation-peak range.
        quant_phase: Offset of the quantizer grid [px] (robustness
            block; 0.0 keeps the exact original code path).
        driver_radar: (line, pixel) radar position whose nearest grid
            node hosts the driver (default: the published position).
            Placement consumes no RNG draws, so varying it leaves
            every random draw unchanged.

    Returns:
        tuple: (data (N, 6) float64 array, info dict). ``info``
        carries the driver row id, its grid node, the truth
        coefficients and the generator parameters.
    """
    rng = np.random.default_rng(seed)
    op = load_offsets_polyfit()
    if driver_radar is None:
        driver_radar = MIN_REPRO_DRIVER_RADAR
    lines = np.linspace(0, grid_shape[0] - 1, n_az).round()
    pixels = np.linspace(0, grid_shape[1] - 1, n_rg).round()
    az_idx = int(np.argmin(np.abs(lines - driver_radar[0])))
    rg_idx = int(np.argmin(np.abs(pixels - driver_radar[1])))
    driver_id = az_idx * n_rg + rg_idx
    ll, pp = np.meshgrid(lines, pixels, indexing="ij")
    ll, pp = ll.ravel(), pp.ravel()
    n = ll.size

    design = op.build_design_matrix(ll, pp, PROD_DEGREE,
                                    0, grid_shape[0], 0, grid_shape[1])
    truth_l = design @ np.asarray(coef_l, dtype=float)
    truth_p = design @ np.asarray(coef_p, dtype=float)

    coherent = np.zeros(n, dtype=bool)
    pick = rng.choice(n, size=int(round(coherent_fraction * n)),
                      replace=False)
    coherent[pick] = True
    coherent[driver_id] = True

    d_l = np.where(
        coherent, truth_l + rng.normal(0.0, coherent_noise_std, n),
        truth_l + rng.uniform(-junk_scatter, junk_scatter, n))
    d_p = np.where(
        coherent, truth_p + rng.normal(0.0, coherent_noise_std, n),
        truth_p + rng.uniform(-junk_scatter, junk_scatter, n))
    peak = np.where(coherent, rng.uniform(*coherent_peak_range, n),
                    rng.uniform(*junk_peak_range, n))
    # The driver measures the truth exactly; after quantization its
    # residual is pure quantization error (|e| <= 1/64 px).
    d_l[driver_id] = truth_l[driver_id]
    d_p[driver_id] = truth_p[driver_id]
    peak[driver_id] = MIN_REPRO_DRIVER_PEAK

    if quant_phase:
        d_l = (np.round((d_l - quant_phase) / OFFSET_QUANTUM)
               * OFFSET_QUANTUM + quant_phase)
        d_p = (np.round((d_p - quant_phase) / OFFSET_QUANTUM)
               * OFFSET_QUANTUM + quant_phase)
    else:
        d_l = np.round(d_l / OFFSET_QUANTUM) * OFFSET_QUANTUM
        d_p = np.round(d_p / OFFSET_QUANTUM) * OFFSET_QUANTUM
    d_l = d_l.astype(np.float32).astype(np.float64)
    d_p = d_p.astype(np.float32).astype(np.float64)
    peak = peak.astype(np.float32).astype(np.float64)
    data = np.column_stack([np.arange(n, dtype=float), ll, pp,
                            d_l, d_p, peak])
    info = {
        "seed": seed, "n_az": n_az, "n_rg": n_rg,
        "n_samples": int(n), "n_coherent": int(coherent.sum()),
        "driver_id": driver_id,
        "driver_node": {"line": float(lines[az_idx]),
                        "pixel": float(pixels[rg_idx])},
        "driver_peak": MIN_REPRO_DRIVER_PEAK,
        "coef_l_true": list(coef_l), "coef_p_true": list(coef_p),
        "coherent_fraction": coherent_fraction,
        "coherent_noise_std": coherent_noise_std,
        "coherent_peak_range": list(coherent_peak_range),
        "junk_scatter": junk_scatter,
        "junk_peak_range": list(junk_peak_range),
        "quant_phase": quant_phase,
        "driver_radar": list(driver_radar),
    }
    return data, info


def judge_min_repro(op, data, driver_id, fit_kwargs,
                    retention_band=(0.03, 0.05), jump_min=0.01):
    """Run the baseline and the -1/32 px flip and judge the criteria.

    The criteria are the VECR design pins of the minimal reproducer:
    the baseline retains 3-5% of the samples with the driver among
    the final inliers; flipping the driver's azimuth offset by one
    Ampcor quantum removes it — in the endgame of the chain, as on
    the real data — forks the removal chain, changes the final
    membership beyond the driver itself, and jumps the fitted
    azimuth surface by a target-class amount.

    Args:
        op: The upstream offsets_polyfit module.
        data: (N, 6) baseline sample matrix.
        driver_id: Row id of the driver sample.
        fit_kwargs: Production fit kwargs.
        retention_band: Accepted (min, max) baseline inlier fraction.
        jump_min: Minimum induced azimuth-field RMS [px].

    Returns:
        dict: Baseline / flip summaries and the per-criterion checks.
    """
    n = len(data)
    base_res, base_trace = polyfit_traced(op, data, **fit_kwargs)
    retention = 1.0 - len(base_res["removed_indices"]) / n
    flipped = data.copy()
    flipped[driver_id, 3] -= OFFSET_QUANTUM
    flip_res, flip_trace = polyfit_traced(op, flipped, **fit_kwargs)

    d_l = flip_res["coefL"] - base_res["coefL"]
    d_p = flip_res["coefP"] - base_res["coefP"]
    stats = coef_delta_stats(op, d_l, d_p)
    inliers_base = set(base_res["inliers"][:, 0].astype(int))
    inliers_flip = set(flip_res["inliers"][:, 0].astype(int))
    membership_delta = (inliers_base ^ inliers_flip) - {driver_id}
    driver_removed = driver_id in flip_res["removed_indices"]
    removal_iteration = (flip_res["removed_indices"].index(driver_id)
                         if driver_removed else None)
    first_div = first_divergence(base_res["removed_indices"],
                                 flip_res["removed_indices"])

    checks = {
        "baseline_driver_survives":
            driver_id not in base_res["removed_indices"],
        "baseline_retention_in_band":
            retention_band[0] <= retention <= retention_band[1],
        "flip_driver_removed": driver_removed,
        # Endgame guard: a driver purged early in the chain would
        # pass the other checks without exercising the observed
        # late-membership mechanism.
        "flip_removal_in_endgame": (
            driver_removed
            and removal_iteration / len(flip_res["removed_indices"])
            >= 0.9),
        "chain_forks": first_div is not None,
        "membership_changed_beyond_driver": len(membership_delta) >= 1,
        "target_class_jump": stats["l"]["rms"] >= jump_min,
    }
    return {
        "baseline": {
            "n_removed": len(base_res["removed_indices"]),
            "retention": retention,
            "n_inliers": len(inliers_base),
            "final_stop_margin": base_trace[-1]["stop_margin"],
            "coefL": [float(c) for c in base_res["coefL"]],
            "coefP": [float(c) for c in base_res["coefP"]],
        },
        "flip": {
            "delta_quanta": -1,
            "n_removed": len(flip_res["removed_indices"]),
            "n_inliers": len(inliers_flip),
            "driver_removal_iteration": removal_iteration,
            "driver_removal_chain_fraction": (
                removal_iteration / len(flip_res["removed_indices"])
                if driver_removed else None),
            "first_divergence": first_div,
            "n_common_inliers": len(inliers_base & inliers_flip),
            "membership_delta_beyond_driver": len(membership_delta),
            "dcoefL": d_l.tolist(), "dcoefP": d_p.tolist(),
            "induced_offset": stats,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def environment_provenance(op):
    """Software environment of a run, for result-JSON provenance.

    Args:
        op: The upstream offsets_polyfit module (its file path and,
            when importable, the isce3 version are recorded).

    Returns:
        dict: Interpreter, numpy/scipy, module and thread info.
    """
    import platform
    import scipy
    try:
        import isce3
        isce3_version = getattr(isce3, "__version__", None)
    except ImportError:
        isce3_version = None
    module_file = getattr(op, "__file__", None)
    if module_file:
        # Home-relative so committed artifacts carry no username.
        module_file = module_file.replace(str(pathlib.Path.home()),
                                          "~")
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "isce3_version": isce3_version,
        "offsets_polyfit_file": module_file,
        "threads": {k: os.environ.get(k) for k in
                    ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                     "MKL_NUM_THREADS")},
    }


def run_min_repro_scenario(args):
    """Run the minimal-reproducer scenario (pinned seed or hunt)."""
    op = load_offsets_polyfit()
    fit_kwargs = production_fit_kwargs()
    retention_band = (args.retention_min, args.retention_max)

    if args.hunt:
        # Seed hunt: report every candidate whose baseline qualifies,
        # so a new pin can be picked when the generator changes. The
        # pinned seed is an existence proof, not a prevalence claim.
        candidates = []
        for seed in range(args.hunt):
            data, info = make_min_repro_case(seed=seed)
            rec = judge_min_repro(op, data, info["driver_id"],
                                  fit_kwargs, retention_band,
                                  args.jump_min)
            entry = {"seed": seed,
                     "retention": rec["baseline"]["retention"],
                     "baseline_driver_survives":
                         rec["checks"]["baseline_driver_survives"],
                     "passed": rec["passed"]}
            if rec["passed"]:
                entry["jump_rms_l"] = (
                    rec["flip"]["induced_offset"]["l"]["rms"])
                entry["driver_removal_chain_fraction"] = (
                    rec["flip"]["driver_removal_chain_fraction"])
            candidates.append(entry)
            log(f"hunt: seed {seed} "
                f"{'PASS' if rec['passed'] else 'fail'}")
        return {"scenario": "minrepro_hunt",
                "n_seeds": args.hunt,
                "n_passed": sum(c["passed"] for c in candidates),
                "candidates": candidates}

    data, info = make_min_repro_case(seed=args.seed)
    # A/A determinism and mirror-equivalence self-proof, as in the
    # other scenarios (the judged fits use the traced mirror).
    up1 = op.polyfit_offsets(data.copy(), max_iterations=len(data),
                             **fit_kwargs)
    up2 = op.polyfit_offsets(data.copy(), max_iterations=len(data),
                             **fit_kwargs)
    aa_identical = (np.array_equal(up1["coefL"], up2["coefL"])
                    and np.array_equal(up1["coefP"], up2["coefP"])
                    and up1["removed_indices"]
                    == up2["removed_indices"])
    mres, _ = polyfit_traced(op, data, **fit_kwargs)
    mirror_equivalent = (
        np.array_equal(up1["coefL"], mres["coefL"])
        and np.array_equal(up1["coefP"], mres["coefP"])
        and up1["removed_indices"] == mres["removed_indices"])

    rec = judge_min_repro(op, data, info["driver_id"], fit_kwargs,
                          retention_band, args.jump_min)
    sigma_l = 0.15 / (PROD_SENSOR["prf"] / PROD_SENSOR["abw"])
    result = {
        "scenario": "minrepro",
        "synthetic": info,
        "fit_kwargs": {k: (float(v) if isinstance(v, float) else v)
                       for k, v in fit_kwargs.items()},
        "environment": environment_provenance(op),
        "aa_identical": aa_identical,
        "mirror_equivalent": mirror_equivalent,
        # The structural inequality behind the endgame purge:
        # crit * sigmaL / w is a leverage-ignoring UPPER BOUND on the
        # exact stop tolerance crit * sigmaL * sqrt(1/w^2 - h_ii),
        # and at the driver's weight even the bound sits below the
        # half-quantum bound q/2 of the nearest-grid quantization
        # error of the input.
        "stop_tolerance_bound_at_driver_px":
            PROD_CRIT_VALUE * sigma_l / MIN_REPRO_DRIVER_PEAK,
        "offset_quantum_px": OFFSET_QUANTUM,
        "half_quantum_px": OFFSET_QUANTUM / 2,
    }
    result.update(rec)
    log(f"minrepro: seed {args.seed} "
        f"{'PASS' if rec['passed'] else 'FAIL'} "
        f"(retention {rec['baseline']['retention']:.3f}, jump rms_l "
        f"{rec['flip']['induced_offset']['l']['rms']:.3e} px)")
    return result


# ---------------------------------------------------------------------
# probe scenario: replay-based follow-up probes as a subcommand
# ---------------------------------------------------------------------

def parse_number_list(text):
    """Parse a comma-separated number list, allowing 'p/q' fractions.

    Args:
        text: E.g. ``"-1/32,-1e-4,0.5"``.

    Returns:
        list[float]: Parsed values.
    """
    out = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "/" in token:
            num, den = token.split("/")
            out.append(float(num) / float(den))
        else:
            out.append(float(token))
    return out


def probe_judge(op, res, base, target_l, target_p):
    """Judgment record of a probed fit against a baseline and target.

    Args:
        op: The upstream offsets_polyfit module.
        res: Probed fit result (upstream shape).
        base: Baseline fit result (upstream shape).
        target_l, target_p: Target coefficient-delta vectors.

    Returns:
        dict: Judgment vs target, induced azimuth RMS, chain fork
        position and removal count.
    """
    d_l = res["coefL"] - base["coefL"]
    d_p = res["coefP"] - base["coefP"]
    return {
        "judgment_vs_target": coef_vector_judgment(
            d_l, d_p, target_l, target_p),
        "induced_rms_l": coef_delta_stats(op, d_l, d_p)["l"]["rms"],
        "first_divergence": first_divergence(
            base["removed_indices"], res["removed_indices"]),
        "n_removed": len(res["removed_indices"]),
    }


def run_probe_scenario(args):
    """Run the replay-based probes at the driver row.

    Reproduces the VECR follow-up probes as a harness subcommand:
    peak-only / pair transplants and delta sweeps at the driver row
    (Rusudan's set), complement transplants, local basin quanta and
    crit_value sweeps with the per-crit flip response (Karasunoendou's
    set). Requires the replay input rasters and logs.
    """
    import time as _time
    op = load_offsets_polyfit()
    coords = build_replay_coordinates(args.rifg, args.ref_rslc)
    fit_kwargs = coords["fit_kwargs"]
    row = args.row

    samples = {"G": extract_replay_samples(args.gpu_dir, coords),
               "A": extract_replay_samples(args.cpu_a_dir, coords),
               "B": extract_replay_samples(args.cpu_b_dir, coords)}
    logged_gpu = parse_logged_coefs(args.gpu_log)
    logged_cpu = parse_logged_coefs(args.cpu_log)
    target_l = (np.array(logged_cpu["coefL"])
                - np.array(logged_gpu["coefL"]))
    target_p = (np.array(logged_cpu["coefP"])
                - np.array(logged_gpu["coefP"]))

    def fit(data, crit=None):
        kwargs = dict(fit_kwargs)
        if crit is not None:
            kwargs["crit_value"] = crit
        t0 = _time.time()
        res = op.polyfit_offsets(data.copy(),
                                 max_iterations=len(data), **kwargs)
        return res, round(_time.time() - t0, 1)

    result = {
        "scenario": "probe", "row": row,
        "row_values": {k: samples[k][row, 3:6].tolist()
                       for k in samples},
        "blas_env": {k: os.environ.get(k) for k in
                     ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                      "MKL_NUM_THREADS")},
    }
    log("probe: base fit (crit default) ...")
    base, secs = fit(samples["G"])
    result["base_seconds"] = secs

    if args.peak_only:
        data = samples["G"].copy()
        data[row, 5] = samples["A"][row, 5]
        res, secs = fit(data)
        result["peak_only"] = (probe_judge(op, res, base, target_l,
                                           target_p)
                               | {"seconds": secs})
        log(f"probe: peak-only rms_l="
            f"{result['peak_only']['induced_rms_l']:.3e}")
    if args.pair:
        data = samples["G"].copy()
        data[row, 3:6] = samples["A"][row, 3:6]
        res, secs = fit(data)
        result["pair"] = (probe_judge(op, res, base, target_l,
                                      target_p) | {"seconds": secs})
        log(f"probe: pair rms_l="
            f"{result['pair']['induced_rms_l']:.3e}")
    if args.complement:
        for key in ("A", "B"):
            donor = samples[key]
            rows = np.nonzero((donor[:, 3] != samples["G"][:, 3])
                              | (donor[:, 4]
                                 != samples["G"][:, 4]))[0]
            others = rows[rows != row]
            data = samples["G"].copy()
            data[others, 3:5] = donor[others, 3:5]
            res, secs = fit(data)
            result[f"complement_{key}"] = (
                probe_judge(op, res, base, target_l, target_p)
                | {"seconds": secs,
                   "n_transplanted": int(others.size)})
            log(f"probe: complement_{key} (n={others.size}) rms_l="
                f"{result[f'complement_{key}']['induced_rms_l']:.3e}")

    deltas = ([("delta", d) for d in args.deltas]
              + [("quanta", k) for k in args.quanta])
    if deltas:
        result["delta_sweep"] = []
        for kind, value in deltas:
            delta = (value * OFFSET_QUANTUM if kind == "quanta"
                     else value)
            data = samples["G"].copy()
            data[row, 3] = samples["G"][row, 3] + delta
            res, secs = fit(data)
            rec = probe_judge(op, res, base, target_l, target_p)
            rec.update({"delta": float(delta), "seconds": secs})
            if kind == "quanta":
                rec["delta_quanta"] = int(value)
            result["delta_sweep"].append(rec)
            log(f"probe: delta {delta:+.6g} rms_l="
                f"{rec['induced_rms_l']:.3e} "
                f"first_div={rec['first_divergence']}")

    if args.crits:
        result["crit_sweep"] = []
        for crit in args.crits:
            b, secs_b = fit(samples["G"], crit=crit)
            data = samples["G"].copy()
            data[row, 3:5] = samples["A"][row, 3:5]
            r, secs_r = fit(data, crit=crit)
            d_l = r["coefL"] - b["coefL"]
            d_p = r["coefP"] - b["coefP"]
            rec = {
                "crit_value": crit,
                "n_removed_base": len(b["removed_indices"]),
                "inlier_fraction":
                    1.0 - len(b["removed_indices"])
                    / len(samples["G"]),
                "flip_response_induced_rms_l":
                    coef_delta_stats(op, d_l, d_p)["l"]["rms"],
                "flip_first_divergence": first_divergence(
                    b["removed_indices"], r["removed_indices"]),
                "seconds": secs_b + secs_r,
            }
            result["crit_sweep"].append(rec)
            log(f"probe: crit {crit} inliers "
                f"{rec['inlier_fraction']:.3f} flip rms "
                f"{rec['flip_response_induced_rms_l']:.3e}")
    return result


# --- membership scenario: summary of recorded removal sequences ----


def file_sha256(path, block=1 << 22):
    """Streaming sha256 hex digest of a file."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def run_membership_scenario(args):
    """Aggregate endgame-membership summary from a recorded replay npz.

    Derives the membership figures cited in the report (final inlier
    counts per chain, pairwise common-inlier counts and first
    divergences, the driver row's removal iteration) from the
    ``removed_*`` / ``samples_*`` arrays a ``replay`` run recorded.
    Pure counting over recorded sequences — no fits are run, and the
    output carries aggregate numbers only (no sample data), so it can
    be published even while the source npz is withheld.
    """
    z = np.load(args.npz)
    keys = sorted(k[len("removed_"):] for k in z.files
                  if k.startswith("removed_"))
    if not keys:
        sys.exit("membership: no removed_* arrays in the npz")
    removed = {k: z[f"removed_{k}"].astype(int).tolist() for k in keys}
    inliers = {k: set(z[f"samples_{k}"][:, 0].astype(int).tolist())
               - set(removed[k]) for k in keys}
    chains = {}
    for k in keys:
        driver_removed = args.driver_id in set(removed[k])
        chains[k] = {
            "n_samples": int(z[f"samples_{k}"].shape[0]),
            "n_removed": len(removed[k]),
            "n_final_inliers": len(inliers[k]),
            "driver_removed": driver_removed,
            "driver_removal_iteration": (
                removed[k].index(args.driver_id)
                if driver_removed else None),
        }
        log(f"membership: {k} inliers {chains[k]['n_final_inliers']} "
            f"driver_iter {chains[k]['driver_removal_iteration']}")
    pairs = {
        f"{a}_vs_{b}": {
            "common_final_inliers": len(inliers[a] & inliers[b]),
            "first_divergence": first_divergence(removed[a],
                                                 removed[b]),
        }
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }
    return {
        "scenario": "membership",
        "npz": args.npz.name,
        "npz_sha256": file_sha256(args.npz),
        "driver_id": args.driver_id,
        "iteration_convention":
            "0-based index into the recorded removal sequence",
        "chains": chains,
        "pairs": pairs,
        "environment": environment_provenance(load_offsets_polyfit()),
    }


def log(message):
    """Progress line on stderr (stdout carries the result JSON)."""
    print(message, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="scenario", required=True)
    pure = sub.add_parser("pure", help="self-contained synthetic")
    pure.add_argument("--n-az", type=int, default=30)
    pure.add_argument("--n-rg", type=int, default=30)
    pure.add_argument("--noise-std", type=float, default=0.03)
    pure.add_argument("--seed", type=int, default=0)
    pure.add_argument("--n-deltas", type=int, default=24)
    pure.add_argument("--json", type=pathlib.Path)
    replay = sub.add_parser("replay", help="real-40k-sample replay")
    for name, default in REPLAY_DEFAULTS.items():
        replay.add_argument(f"--{name.replace('_', '-')}",
                            type=pathlib.Path, default=default,
                            dest=name)
    replay.add_argument("--gate-only", action="store_true")
    replay.add_argument("--flip-node", default="423,95",
                        help="raw offsets-grid 'line,sample' of the "
                             "microscope node")
    replay.add_argument("--max-hybrids", type=int, default=16)
    replay.add_argument("--json", type=pathlib.Path)
    replay.add_argument("--npz", type=pathlib.Path)
    minrepro = sub.add_parser(
        "minrepro", help="public minimal synthetic reproducer (L0)")
    minrepro.add_argument("--seed", type=int, default=MIN_REPRO_SEED)
    minrepro.add_argument("--hunt", type=int, default=0,
                          help="sweep seeds 0..N-1 and report the "
                               "qualifying candidates instead of "
                               "judging one pinned seed")
    minrepro.add_argument("--jump-min", type=float, default=0.01,
                          help="minimum induced azimuth RMS [px] for "
                               "the target-class-jump check")
    minrepro.add_argument("--retention-min", type=float, default=0.03)
    minrepro.add_argument("--retention-max", type=float, default=0.05)
    minrepro.add_argument("--json", type=pathlib.Path)
    probe = sub.add_parser(
        "probe", help="replay-based probes at the driver row")
    for name, default in REPLAY_DEFAULTS.items():
        if name in ("rifg", "ref_rslc", "gpu_dir", "cpu_a_dir",
                    "cpu_b_dir", "gpu_log", "cpu_log"):
            probe.add_argument(f"--{name.replace('_', '-')}",
                               type=pathlib.Path, default=default,
                               dest=name)
    probe.add_argument("--row", type=int, default=22961)
    probe.add_argument("--peak-only", action="store_true")
    probe.add_argument("--pair", action="store_true")
    probe.add_argument("--complement", action="store_true")
    probe.add_argument("--deltas", type=parse_number_list,
                       default=[],
                       help="comma list of dL deltas [px]; 'p/q' "
                            "fractions allowed (e.g. -1/32)")
    probe.add_argument("--quanta", type=parse_number_list,
                       default=[],
                       help="comma list of dL deltas in 1/32 px "
                            "quanta (e.g. -2,1,2)")
    probe.add_argument("--crits", type=parse_number_list,
                       default=[],
                       help="comma list of crit_value sweep points")
    probe.add_argument("--json", type=pathlib.Path)
    membership = sub.add_parser(
        "membership",
        help="aggregate membership summary from a recorded replay npz")
    membership.add_argument("--npz", type=pathlib.Path, required=True,
                            help="npz written by the replay scenario")
    membership.add_argument("--driver-id", type=int, default=22961)
    membership.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args()

    if args.scenario == "pure":
        result, arrays = run_pure_scenario(args), {}
    elif args.scenario == "replay":
        result, arrays = run_replay_scenario(args)
    elif args.scenario == "minrepro":
        result, arrays = run_min_repro_scenario(args), {}
    elif args.scenario == "membership":
        result, arrays = run_membership_scenario(args), {}
    else:
        result, arrays = run_probe_scenario(args), {}
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    if getattr(args, "npz", None) and arrays:
        args.npz.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(args.npz, **arrays)
    if args.scenario == "minrepro" and not args.hunt \
            and not result["passed"]:
        sys.exit("minrepro: one or more criteria checks FAILED")


if __name__ == "__main__":
    main()
