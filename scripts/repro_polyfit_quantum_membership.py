#!/usr/bin/env python3
"""
Self-contained minimal reproducer: a single one-quantum (-1/32 px)
input flip can discontinuously jump the isce3 rubbersheet offsets
polyfit.

The NISAR InSAR rubbersheet step fits two degree-2 surfaces to the
dense-offsets field with `isce3.math.offsets_polyfit.polyfit_offsets`
(corr_peak-weighted LSQ + sequential worst-outlier removal, w-test
stop `max|w| <= crit_value`, production default crit_value = 0.1).
On a real NISAR L-SAR pair (ASC 139/019) processed once with the CPU
and once with the GPU InSAR workflow, the two runs' RIFG
`pixelOffsets` layers differed by a smooth ~3.6e-2 px RMS degree-2
surface. In a controlled replay of the production fit, transplanting
ONE isolated CPU-Ampcor sample — a high-correlation window whose
subpixel peak moved by exactly one Ampcor correlation-grid quantum
(1/32 px) — into the GPU baseline was necessary and sufficient to
reproduce the observed coefficient difference to the available
production log precision: the sequential rejection chain forks near
its end and the final inlier membership — the set that defines the
fit — changes wholesale.

This script reproduces that mechanism with 900 synthetic samples
inside an isce3 Python environment (NumPy, and SciPy transitively
via the module under test) — nothing else. The sample set imitates
the production composition: a small coherent elite (high correlation
peaks, ~0.02 px scatter around a smooth degree-2 truth) inside a
junk majority (low peaks, offsets scattered over the Ampcor
search-window scale), all offsets quantized to the 1/32 px
correlation grid and stored through the production float32 path.
One coherent sample — the *driver* — sits at the grid node nearest
the real driver's radar position (line 23405 of 41040, pixel 42589
of 52906), carries the real driver's correlation peak (0.9485) and
measures the truth surface exactly. The fit uses the values the
production workflow passes to polyfit_offsets, rebuilt from the
RIFG/RSLC files (crit 0.1, sigmaL = 0.12470527678472171 px /
sigmaP = 0.08333333333333334 px, full-radar-grid normalization).

The structural condition is the same inequality as production: the
exact w-test stop tolerance at a sample is
crit * sigma * sqrt(1/w^2 - h_ii), which at the driver's weight is
at most crit * sigmaL / 0.9485 = 0.0132 px — below the half-bin
bound q/2 = 0.0156 px on the nearest-grid quantization error of its
input. A high-weight sample can therefore fail the stop test on
quantization error alone; the observed 95.8% production purge shows
that susceptibility was realized on the real data, leaving the
final membership on a knife edge.

Expected output (pinned seed 29; identical numbers are produced by
the `minrepro` subcommand of scripts/polyfit_sensitivity.py):

    baseline : n_removed 868/900 (retention 3.6%), driver survives
    flip     : driver removed at iteration 832 of 870 (95.6% of the
               chain), first fork at iteration 83, final inliers
               32 vs 30 with 25 common
    jump     : induced azimuth surface RMS 2.75e-2 px, max 0.106 px
    checks   : 8/8 PASS

The pinned seed is an existence proof of the mechanism at 900
samples, found by a documented 40-seed calibration hunt (7/40 seeds
pass all criteria); no prevalence claim is made. The real-data (L1)
evidence lives in the replay artifacts next to this script.

Usage:

    python3 scripts/repro_polyfit_quantum_membership.py [--json out]
        [--seed 29]

Imports `isce3.math.offsets_polyfit` from an installed isce3 if
available, else directly from the source tree (env ISCE3_PY_SRC or
the checkout that contains this benchmark directory).
"""

import argparse
import importlib.util
import json
import os
import pathlib
import sys

import numpy as np

# Production fit configuration — the exact values the NISAR ASC
# 139/019 workflow passes to polyfit_offsets, rebuilt from the
# RIFG/RSLC files (abw = processedAzimuthBandwidth; rsr =
# c / (2 * slantRangeSpacing), exactly 48 MHz).
PROD_SENSOR = {"prf": 1520.0, "abw": 1263.68013808518,
               "rsr": 48000000.0, "rbw": 40000000.0}
PROD_GRID_SHAPE = (41040, 52906)
PROD_DEGREE = 2
PROD_CRIT_VALUE = 0.1
OFFSET_QUANTUM = 1.0 / 32.0  # Ampcor correlation-surface grid [px]

# The real driver node of the L1 replay: sample row 22961, radar
# (23405, 42589), corr_peak 0.9485, flipped by exactly -1/32 px in
# azimuth between the CPU and GPU Ampcor runs.
DRIVER_RADAR = (23405, 42589)
DRIVER_PEAK = 0.9485
PINNED_SEED = 29

# Generator parameters (calibrated; see minrepro_hunt40.json).
N_AZ = N_RG = 30
COEF_L_TRUE = (0.4, 1.2, -0.6, 0.3, -0.2, 0.15)
COEF_P_TRUE = (-0.2, 0.5, 0.8, -0.25, 0.1, -0.3)
COHERENT_FRACTION = 0.08
COHERENT_NOISE_STD = 0.02
COHERENT_PEAK_RANGE = (0.3, 0.8)
JUNK_SCATTER = 20.0
JUNK_PEAK_RANGE = (0.02, 0.15)


def load_offsets_polyfit():
    """Import the upstream isce3 offsets_polyfit module under test."""
    try:
        from isce3.math import offsets_polyfit
        return offsets_polyfit
    except ImportError:
        pass
    root = pathlib.Path(os.environ.get(
        "ISCE3_PY_SRC",
        pathlib.Path(__file__).resolve().parents[2]
        / "python/packages"))
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


def make_case(op, seed):
    """Build the two-population sample set; returns (data, driver_id).

    Bit-identical to make_min_repro_case of polyfit_sensitivity.py
    (same RNG call order).
    """
    rng = np.random.default_rng(seed)
    gs = PROD_GRID_SHAPE
    lines = np.linspace(0, gs[0] - 1, N_AZ).round()
    pixels = np.linspace(0, gs[1] - 1, N_RG).round()
    az_idx = int(np.argmin(np.abs(lines - DRIVER_RADAR[0])))
    rg_idx = int(np.argmin(np.abs(pixels - DRIVER_RADAR[1])))
    driver_id = az_idx * N_RG + rg_idx
    ll, pp = np.meshgrid(lines, pixels, indexing="ij")
    ll, pp = ll.ravel(), pp.ravel()
    n = ll.size

    design = op.build_design_matrix(ll, pp, PROD_DEGREE,
                                    0, gs[0], 0, gs[1])
    truth_l = design @ np.asarray(COEF_L_TRUE, dtype=float)
    truth_p = design @ np.asarray(COEF_P_TRUE, dtype=float)

    coherent = np.zeros(n, dtype=bool)
    pick = rng.choice(n, size=int(round(COHERENT_FRACTION * n)),
                      replace=False)
    coherent[pick] = True
    coherent[driver_id] = True

    d_l = np.where(
        coherent, truth_l + rng.normal(0.0, COHERENT_NOISE_STD, n),
        truth_l + rng.uniform(-JUNK_SCATTER, JUNK_SCATTER, n))
    d_p = np.where(
        coherent, truth_p + rng.normal(0.0, COHERENT_NOISE_STD, n),
        truth_p + rng.uniform(-JUNK_SCATTER, JUNK_SCATTER, n))
    peak = np.where(coherent, rng.uniform(*COHERENT_PEAK_RANGE, n),
                    rng.uniform(*JUNK_PEAK_RANGE, n))
    # The driver measures the truth exactly: after quantization its
    # measurement error w.r.t. the generating truth is pure
    # nearest-grid quantization error (|e| <= 1/64 px). (This is not
    # the w-test residual, which is taken against the currently
    # fitted surface.)
    d_l[driver_id] = truth_l[driver_id]
    d_p[driver_id] = truth_p[driver_id]
    peak[driver_id] = DRIVER_PEAK

    d_l = np.round(d_l / OFFSET_QUANTUM) * OFFSET_QUANTUM
    d_p = np.round(d_p / OFFSET_QUANTUM) * OFFSET_QUANTUM
    d_l = d_l.astype(np.float32).astype(np.float64)
    d_p = d_p.astype(np.float32).astype(np.float64)
    peak = peak.astype(np.float32).astype(np.float64)
    data = np.column_stack([np.arange(n, dtype=float), ll, pp,
                            d_l, d_p, peak])
    return data, driver_id


def induced_field(op, dcoef_l, dcoef_p, n_eval=101):
    """RMS / max of the offset field induced by a coefficient delta."""
    gs = PROD_GRID_SHAPE
    ll, pp = np.meshgrid(np.linspace(0, gs[0], n_eval),
                         np.linspace(0, gs[1], n_eval), indexing="ij")
    d_l, _ = op.predict_offsets(ll, pp, dcoef_l, dcoef_p, PROD_DEGREE,
                                0, gs[0], 0, gs[1])
    v = d_l.ravel()
    return {"rms": float(np.sqrt(np.mean(v ** 2))),
            "max_abs": float(np.abs(v).max())}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0])
    ap.add_argument("--seed", type=int, default=PINNED_SEED)
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args()

    op = load_offsets_polyfit()
    fit_kwargs = {
        "degree": PROD_DEGREE, "crit_value": PROD_CRIT_VALUE,
        "minL": 0, "maxL": PROD_GRID_SHAPE[0],
        "minP": 0, "maxP": PROD_GRID_SHAPE[1],
        "prf": PROD_SENSOR["prf"], "abw": PROD_SENSOR["abw"],
        "rsr": PROD_SENSOR["rsr"], "rbw": PROD_SENSOR["rbw"],
    }
    data, driver_id = make_case(op, args.seed)
    n = len(data)

    def fit(d):
        return op.polyfit_offsets(d.copy(), max_iterations=len(d),
                                  **fit_kwargs)

    base = fit(data)
    rerun = fit(data)
    aa_identical = (np.array_equal(base["coefL"], rerun["coefL"])
                    and np.array_equal(base["coefP"], rerun["coefP"])
                    and base["removed_indices"]
                    == rerun["removed_indices"])

    flipped = data.copy()
    flipped[driver_id, 3] -= OFFSET_QUANTUM
    flip = fit(flipped)

    retention = 1.0 - len(base["removed_indices"]) / n
    inliers_base = set(base["inliers"][:, 0].astype(int))
    inliers_flip = set(flip["inliers"][:, 0].astype(int))
    membership_delta = (inliers_base ^ inliers_flip) - {driver_id}
    driver_removed = driver_id in flip["removed_indices"]
    removal_iteration = (flip["removed_indices"].index(driver_id)
                         if driver_removed else None)
    first_div = next(
        (i for i, (a, b) in enumerate(zip(base["removed_indices"],
                                          flip["removed_indices"]))
         if a != b), None)
    if first_div is None and (len(base["removed_indices"])
                              != len(flip["removed_indices"])):
        # One sequence is a proper prefix of the other: the chains
        # fork where the shorter one stops.
        first_div = min(len(base["removed_indices"]),
                        len(flip["removed_indices"]))
    jump = induced_field(op, flip["coefL"] - base["coefL"],
                         flip["coefP"] - base["coefP"])
    sigma_l = 0.15 / (PROD_SENSOR["prf"] / PROD_SENSOR["abw"])

    checks = {
        "aa_identical": aa_identical,
        "baseline_driver_survives":
            driver_id not in base["removed_indices"],
        "baseline_retention_in_band": 0.03 <= retention <= 0.05,
        "flip_driver_removed": driver_removed,
        # Endgame guard: a driver purged early in the chain would
        # pass the other checks without exercising the observed
        # late-membership mechanism.
        "flip_removal_in_endgame": (
            driver_removed
            and removal_iteration / len(flip["removed_indices"])
            >= 0.9),
        "chain_forks": first_div is not None,
        "membership_changed_beyond_driver": len(membership_delta) >= 1,
        "target_class_jump": jump["rms"] >= 0.01,
    }
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
    result = {
        "seed": args.seed, "n_samples": n, "driver_id": driver_id,
        "driver_peak": DRIVER_PEAK,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "isce3_version": isce3_version,
            "offsets_polyfit_file": module_file,
            "threads": {k: os.environ.get(k) for k in
                        ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                         "MKL_NUM_THREADS")},
        },
        # Leverage-ignoring upper bound of the exact stop tolerance
        # crit * sigmaL * sqrt(1/w^2 - h_ii) at the driver's weight.
        "stop_tolerance_bound_at_driver_px":
            PROD_CRIT_VALUE * sigma_l / DRIVER_PEAK,
        "half_quantum_px": OFFSET_QUANTUM / 2,
        "baseline": {"n_removed": len(base["removed_indices"]),
                     "retention": retention,
                     "n_inliers": len(inliers_base),
                     "coefL": base["coefL"].tolist(),
                     "coefP": base["coefP"].tolist()},
        "flip": {"delta_px": -OFFSET_QUANTUM,
                 "n_removed": len(flip["removed_indices"]),
                 "n_inliers": len(inliers_flip),
                 "driver_removal_iteration": removal_iteration,
                 "first_divergence": first_div,
                 "n_common_inliers": len(inliers_base & inliers_flip),
                 "membership_delta_beyond_driver":
                     len(membership_delta),
                 "induced_azimuth_field_px": jump,
                 "dcoefL": (flip["coefL"] - base["coefL"]).tolist(),
                 "dcoefP": (flip["coefP"] - base["coefP"]).tolist()},
        "checks": checks,
        "passed": all(checks.values()),
    }

    print(f"baseline : n_removed {result['baseline']['n_removed']}"
          f"/{n} (retention {retention:.1%}), driver "
          f"{'SURVIVES' if checks['baseline_driver_survives'] else 'removed'}")
    if driver_removed:
        print(f"flip     : driver removed at iteration "
              f"{removal_iteration} of "
              f"{result['flip']['n_removed']}, first fork at "
              f"iteration {first_div}, final inliers "
              f"{len(inliers_base)} vs {len(inliers_flip)} with "
              f"{result['flip']['n_common_inliers']} common")
    else:
        print("flip     : driver NOT removed")
    print(f"jump     : induced azimuth RMS {jump['rms']:.3e} px, "
          f"max {jump['max_abs']:.3e} px")
    n_pass = sum(checks.values())
    print(f"checks   : {n_pass}/{len(checks)} "
          f"{'PASS' if result['passed'] else 'FAIL'}")
    for name, ok in checks.items():
        print(f"  [{'x' if ok else ' '}] {name}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n",
                             encoding="utf-8")
    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
