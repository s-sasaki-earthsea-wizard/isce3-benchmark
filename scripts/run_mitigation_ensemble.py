"""Ensemble runner for the polyfit mitigation comparison.

Executes the pre-registered evaluation of
``docs/polyfit-mitigation-prereg.md`` (sections 4-6): per case (seed)
it generates the two-population synthetic, builds the frozen flip
manifest, runs every candidate on the base case and on every flip,
and writes one JSON per seed with the frozen metrics and full
provenance. Failures are retained as data (a failed fit becomes an
``error`` record, never a dropped seed).

Modes:

* ``ensemble`` — confirmatory seeds (default 1000:1200, unfiltered)
  or any explicit range; ``--cell`` selects a pre-registered
  robustness-block generator variant (default seeds 1200:1250).
* ``real40k`` — the recorded 40k case study: base fit + the recorded
  driver flip (row 22961, azimuth, -1/32 px) per candidate from a
  local (unpublished) npz.

Confirmatory numbers are produced in the pinned container
environment with threads pinned to 1 (set OMP_NUM_THREADS=1 etc.
before running; the environment is recorded in the provenance
block). Use ``--jobs N`` for per-seed process parallelism.
"""

import argparse
import json
import multiprocessing
import os
import pathlib
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from polyfit_sensitivity import (OFFSET_QUANTUM, PROD_GRID_SHAPE,  # noqa: E402
                                 load_offsets_polyfit,
                                 make_min_repro_case,
                                 production_fit_kwargs)
from polyfit_mitigation import (CANDIDATES, Policy, apply_flip,  # noqa: E402
                                build_flip_manifest, huber_irls_fit,
                                polyfit_candidate)

# Frozen metric constants (pre-registration section 6).
EVAL_GRID_N = 101
MATERIAL_JUMP_RMS = 1e-2  # px, either band

# Candidate roster (section 3). C0 is always evaluated first: it is
# the drift/membership reference for every other candidate.
EVAL_CANDIDATES = ("C0", "C2b", "C3", "C4", "C4b", "C4+C3", "C5", "C6")

# Pre-registered robustness-block generator variants (section 4).
ROBUSTNESS_CELLS = {
    "quant_phase": {"quant_phase": OFFSET_QUANTUM / 2.0},
    "driver_corner": {"driver_radar": (PROD_GRID_SHAPE[0] - 1.0,
                                       PROD_GRID_SHAPE[1] - 1.0)},
    "elite_low": {"coherent_fraction": 0.04},
    "elite_high": {"coherent_fraction": 0.16},
}

_GRID_DESIGN = {}


def eval_grid_design(op, grid_shape=PROD_GRID_SHAPE, n=EVAL_GRID_N):
    """Design matrix of the fixed surface-evaluation grid (cached)."""
    key = (grid_shape, n)
    if key not in _GRID_DESIGN:
        gl, gp = np.meshgrid(np.linspace(0, grid_shape[0], n),
                             np.linspace(0, grid_shape[1], n),
                             indexing="ij")
        _GRID_DESIGN[key] = op.build_design_matrix(
            gl.ravel(), gp.ravel(), 2, 0, grid_shape[0],
            0, grid_shape[1])
    return _GRID_DESIGN[key]


def surface_rms(design, dcoef_l, dcoef_p):
    """Per-band RMS over the evaluation grid of a coefficient delta.

    The predicted surface is linear in the coefficients, so the
    surface difference of two fits is the surface of their
    coefficient difference.
    """
    rms_l = float(np.sqrt(np.mean((design @ np.asarray(dcoef_l)) ** 2)))
    rms_p = float(np.sqrt(np.mean((design @ np.asarray(dcoef_p)) ** 2)))
    return rms_l, rms_p


def run_fit(op, name, data, fit_kwargs):
    """Run one candidate on one input; failures become records."""
    try:
        if name == "C5":
            kw = {k: v for k, v in fit_kwargs.items()
                  if k != "crit_value"}
            return huber_irls_fit(op, data, **kw)
        if name == "C6":
            res, _ = polyfit_candidate(op, data, policy=Policy(),
                                       max_iterations=0, **fit_kwargs)
            return res
        res, _ = polyfit_candidate(op, data, policy=CANDIDATES[name],
                                   **fit_kwargs)
        return res
    except (ValueError, np.linalg.LinAlgError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _base_summary(res, design, c0, truth_l, truth_p):
    """JSON-safe summary of a candidate's base fit."""
    if "error" in res:
        return {"error": res["error"]}
    drift = (surface_rms(design, res["coefL"] - c0["coefL"],
                         res["coefP"] - c0["coefP"])
             if c0 is not None else (0.0, 0.0))
    truth = surface_rms(design, res["coefL"] - truth_l,
                        res["coefP"] - truth_p)
    membership = sorted(int(i) for i in res["inliers"][:, 0])
    summary = {
        "coefL": [float(v) for v in res["coefL"]],
        "coefP": [float(v) for v in res["coefP"]],
        "stop_reason": res.get("stop_reason"),
        "converged": bool(res.get("converged")),
        "n_removed": int(res.get("n_removed", 0)),
        "retention": float(res.get("retention", 1.0)),
        "refits": int(res.get("refits", 0)),
        "seconds": float(res.get("seconds", float("nan"))),
        "normal_matrix_cond": res.get("normal_matrix_cond"),
        "ridge_fallbacks": res.get("ridge_fallbacks"),
        "spatial_coverage": res.get("spatial_coverage"),
        "drift_rms_l": drift[0], "drift_rms_p": drift[1],
        "truth_rms_l": truth[0], "truth_rms_p": truth[1],
        "inlier_ids": membership,
    }
    if "ess_kish" in res:
        summary["ess_kish"] = res["ess_kish"]
        summary["downweight_quantiles"] = res["downweight_quantiles"]
        summary["n_downweighted"] = res["n_downweighted"]
        summary["inlier_ids"] = None  # membership undefined for IRLS
    if res.get("batch_sizes"):
        summary["max_batch"] = int(max(res["batch_sizes"]))
        summary["final_batch_overshoot"] = res["final_batch_overshoot"]
    return summary


def _flip_summary(flip, res, base_res, design):
    if "error" in res:
        return {**{k: flip[k] for k in
                   ("estimand", "node", "band", "sign")},
                "error": res["error"]}
    rms_l, rms_p = surface_rms(design,
                               res["coefL"] - base_res["coefL"],
                               res["coefP"] - base_res["coefP"])
    return {
        "estimand": flip["estimand"], "node": flip["node"],
        "band": flip["band"], "sign": flip["sign"],
        "response_rms_l": rms_l, "response_rms_p": rms_p,
        "material": bool(rms_l > MATERIAL_JUMP_RMS
                         or rms_p > MATERIAL_JUMP_RMS),
        "stop_reason": res.get("stop_reason"),
        "n_removed": int(res.get("n_removed", 0)),
    }


def _jaccard(a, b):
    if a is None or b is None:
        return None
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else None


def provenance(op):
    """Environment/provenance block recorded into every output."""
    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root,
            capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = None, None
    return {
        "generator_commit": sha, "worktree_dirty": dirty,
        "numpy": np.__version__,
        "python": sys.version.split()[0],
        "upstream_module": getattr(op, "__file__", None),
        "thread_env": {k: os.environ.get(k) for k in
                       ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                        "MKL_NUM_THREADS")},
        "material_jump_rms": MATERIAL_JUMP_RMS,
        "eval_grid_n": EVAL_GRID_N,
    }


def evaluate_case(seed, gen_kwargs=None, candidates=EVAL_CANDIDATES,
                  n_uniform=10, n_stratified=10):
    """Run every candidate on one case and its manifest flips."""
    op = load_offsets_polyfit()
    gen_kwargs = dict(gen_kwargs or {})
    data, info = make_min_repro_case(seed=seed, **gen_kwargs)
    grid_shape = gen_kwargs.get("grid_shape", PROD_GRID_SHAPE)
    design = eval_grid_design(op, grid_shape)
    manifest = build_flip_manifest(seed, data[:, 5],
                                   n_uniform=n_uniform,
                                   n_stratified=n_stratified)
    driver_flip = {"estimand": "driver", "node": info["driver_id"],
                   "band": "L", "sign": -1,
                   "delta": -OFFSET_QUANTUM}
    fit_kwargs = production_fit_kwargs(grid_shape)
    truth_l = np.asarray(info["coef_l_true"], dtype=float)
    truth_p = np.asarray(info["coef_p_true"], dtype=float)

    names = list(candidates)
    if "C0" not in names:
        names.insert(0, "C0")
    elif names[0] != "C0":
        names.remove("C0")
        names.insert(0, "C0")

    out = {"seed": int(seed), "generator": info,
           "manifest_sha256": manifest["sha256"],
           "candidates": {}}
    c0_base, c0_summary = None, None
    for name in names:
        base = run_fit(op, name, data, fit_kwargs)
        summary = _base_summary(base, design, c0_base, truth_l,
                                truth_p)
        flips = []
        if "error" not in base:
            for flip in manifest["flips"] + [driver_flip]:
                fres = run_fit(op, name, apply_flip(data, flip),
                               fit_kwargs)
                flips.append(_flip_summary(flip, fres, base, design))
        if name == "C0":
            c0_base, c0_summary = base, summary
            summary["drift_rms_l"] = summary["drift_rms_p"] = 0.0
        summary["jaccard_vs_c0"] = _jaccard(
            summary.get("inlier_ids"),
            c0_summary.get("inlier_ids") if c0_summary else None)
        out["candidates"][name] = {"base": summary, "flips": flips}
    return out


def _run_one_seed(args):
    seed, gen_kwargs, out_dir, candidates = args
    path = pathlib.Path(out_dir) / f"seed{seed:05d}.json"
    if path.exists():
        return f"seed {seed}: exists, skipped"
    t0 = time.perf_counter()
    result = evaluate_case(seed, gen_kwargs, candidates)
    result["provenance"] = provenance(load_offsets_polyfit())
    result["wall_seconds"] = time.perf_counter() - t0
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=1, sort_keys=True))
    tmp.rename(path)
    return f"seed {seed}: done in {result['wall_seconds']:.1f}s"


def cmd_ensemble(args):
    lo, hi = (int(v) for v in args.seeds.split(":"))
    gen_kwargs = ROBUSTNESS_CELLS[args.cell] if args.cell else {}
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = tuple(args.candidates.split(",")) if args.candidates \
        else EVAL_CANDIDATES
    tasks = [(seed, gen_kwargs, str(out_dir), candidates)
             for seed in range(lo, hi)]
    if args.jobs > 1:
        ctx = multiprocessing.get_context("fork")
        with ctx.Pool(args.jobs) as pool:
            for msg in pool.imap_unordered(_run_one_seed, tasks):
                print(msg, file=sys.stderr)
    else:
        for task in tasks:
            print(_run_one_seed(task), file=sys.stderr)


def cmd_real40k(args):
    op = load_offsets_polyfit()
    z = np.load(args.npz)
    data = np.asarray(z["samples_G"], dtype=float)
    row = args.driver_row
    driver_flip = {"estimand": "recorded_driver", "node": row,
                   "band": "L", "sign": -1, "delta": -OFFSET_QUANTUM}
    design = eval_grid_design(op)
    fit_kwargs = production_fit_kwargs()
    candidates = tuple(args.candidates.split(",")) if args.candidates \
        else EVAL_CANDIDATES
    names = list(candidates)
    if "C0" not in names:
        names.insert(0, "C0")

    out = {"scenario": "real40k", "npz": str(args.npz),
           "driver_row": row, "n_samples": int(data.shape[0]),
           "candidates": {}}
    c0_base = None
    for name in names:
        print(f"real40k: {name} base fit ...", file=sys.stderr)
        base = run_fit(op, name, data, fit_kwargs)
        if "error" in base:
            out["candidates"][name] = {"base": base}
            continue
        drift = (surface_rms(design, base["coefL"] - c0_base["coefL"],
                             base["coefP"] - c0_base["coefP"])
                 if c0_base is not None else (0.0, 0.0))
        print(f"real40k: {name} driver flip ...", file=sys.stderr)
        flip = run_fit(op, name, apply_flip(data, driver_flip),
                       fit_kwargs)
        entry = {
            "base": {
                "coefL": [float(v) for v in base["coefL"]],
                "coefP": [float(v) for v in base["coefP"]],
                "stop_reason": base.get("stop_reason"),
                "n_removed": int(base.get("n_removed", 0)),
                "retention": float(base.get("retention", 1.0)),
                "refits": int(base.get("refits", 0)),
                "seconds": float(base.get("seconds", float("nan"))),
                "drift_rms_l": drift[0], "drift_rms_p": drift[1],
            },
            "driver_flip": _flip_summary(driver_flip, flip, base,
                                         design),
        }
        if "ess_kish" in base:
            entry["base"]["ess_kish"] = base["ess_kish"]
        if base.get("batch_sizes"):
            entry["base"]["max_batch"] = int(max(base["batch_sizes"]))
        out["candidates"][name] = entry
        if name == "C0":
            c0_base = base
    out["provenance"] = provenance(op)
    pathlib.Path(args.out).write_text(
        json.dumps(out, indent=1, sort_keys=True))
    print(f"wrote {args.out}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    ens = sub.add_parser("ensemble", help="synthetic seed ensemble")
    ens.add_argument("--seeds", default="1000:1200",
                     help="seed range lo:hi (hi exclusive)")
    ens.add_argument("--cell", choices=sorted(ROBUSTNESS_CELLS),
                     default=None,
                     help="robustness-block generator variant")
    ens.add_argument("--out", required=True, help="output directory")
    ens.add_argument("--jobs", type=int, default=1)
    ens.add_argument("--candidates", default=None,
                     help="comma list (default: full roster)")
    ens.set_defaults(func=cmd_ensemble)

    r40 = sub.add_parser("real40k", help="recorded 40k case study")
    r40.add_argument("--npz", required=True,
                     help="replay_real40k.npz (local, unpublished)")
    r40.add_argument("--driver-row", type=int, default=22961)
    r40.add_argument("--out", required=True, help="output JSON file")
    r40.add_argument("--candidates", default=None)
    r40.set_defaults(func=cmd_real40k)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
