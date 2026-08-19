"""Aggregate the mitigation-ensemble outputs into the frozen report
statistics.

Consumes the per-seed JSONs written by ``run_mitigation_ensemble.py``
plus the ``real40k.json`` case study and produces, per candidate:

* flip-response distributions (p50/p90/p99/max per band) separately
  per estimand (uniform / stratified / driver), with the frozen
  material-jump exceedance rates and counts, exact-zero-in-both-bands
  counts, seed-paired material-rate differences vs C0 with
  cluster-bootstrap 95% CIs, and paired transition counts
  (persistent / resolved / introduced material events vs C0);
* unperturbed drift and truth-error distributions over seeds;
* the pre-registered always-reported diagnostics (normal-matrix
  condition number, spatial coverage, final-batch overshoot, Kish
  ESS where defined), termination health including flip stop
  reasons, runtime, membership-Jaccard and retention summaries;
* the pre-registered decision-gate evaluation
  (``docs/polyfit-mitigation-prereg.md`` section 7) against the C0
  reference and the 40k case study — gate g4 covers base AND flip
  stop reasons;
* a machine-readable provenance distribution (HEAD × dirty counts
  and the tracked generating-input blob IDs per observed HEAD).

Quantiles are the frozen report set; ranking statistics are the
pre-registered p99 and the material-jump exceedance rate — sample
maxima are reported, never ranked on. The seed is the resampling
cluster: rates are pooled over flips, per-seed counts are retained,
and the bootstrap resamples seeds.
"""

import argparse
import json
import pathlib
import subprocess
import sys

import numpy as np

# Frozen constants (pre-registration sections 6-7).
MATERIAL_JUMP_RMS = 1e-2      # px
GATE_RATE_FACTOR = 0.1        # candidate rate <= 0.1 x C0 rate
GATE_DRIFT_RMS = 3.6e-2       # px, seed-median and 40k
GATE_TRUTH_FACTOR = 1.05      # x C0 seed-median
GATE_RUNTIME_FACTOR = 1.5     # x C0 on the 40k case
QUANTILES = (0.5, 0.9, 0.99)
BOOTSTRAP_B = 10000
BOOTSTRAP_SEED = 20260819     # fixed: the bootstrap must be replayable

ESTIMANDS = ("uniform", "stratified", "driver")
# Tracked inputs whose blob identity across observed HEADs supports
# treating a commit-spanning archive as one scheduled run.
GENERATING_INPUTS = ("scripts/polyfit_sensitivity.py",
                     "scripts/polyfit_mitigation.py",
                     "scripts/run_mitigation_ensemble.py",
                     "docs/polyfit-mitigation-prereg.md")

# Candidate-aware healthy terminations for gate g4: anything outside
# the allow-list (including missing or unknown stop reasons) fails
# the gate. Guard stops are healthy only for the guard candidate;
# budget exhaustion (max_refits, irls_max_iterations) and
# rank/factorization failures are never healthy.
HEALTHY_STOPS = {
    "C0": {"w_test"},
    "C2b": {"w_test", "min_inliers", "max_rejections"},
    "C3": {"w_test"},
    "C4": {"w_test"},
    "C4b": {"w_test"},
    "C4+C3": {"w_test"},
    "C5": {"irls_converged"},
    "C6": {"w_test"},
}
DEFAULT_HEALTHY = {"w_test"}


def validate_archive(seed_results):
    """Fail closed on incomplete or inconsistent archives.

    Requires every seed file to carry the same candidate set, a
    successful C0 base per seed, and — for every candidate whose
    base fit succeeded — flip keysets per estimand identical to
    C0's manifest keys for that seed. A favorable subset must never
    be able to pass the gates silently.

    Raises:
        ValueError: On any missing candidate, missing flip, or
            manifest mismatch.
    """
    if not seed_results:
        raise ValueError("empty archive")
    names = set(seed_results[0]["candidates"].keys())
    for seed_result in seed_results:
        seed = seed_result.get("seed")
        present = set(seed_result["candidates"].keys())
        if present != names:
            raise ValueError(
                f"seed {seed}: candidate set {sorted(present)} != "
                f"{sorted(names)}")
        c0 = seed_result["candidates"].get("C0")
        if c0 is None or "error" in c0["base"]:
            raise ValueError(f"seed {seed}: no successful C0 base")
        ref_keys = {e: set() for e in ESTIMANDS}
        for flip in c0["flips"]:
            if "error" not in flip:
                ref_keys[flip["estimand"]].add(_flip_key(flip))
        for name in names:
            cand = seed_result["candidates"][name]
            if "error" in cand["base"]:
                continue
            keys = {e: set() for e in ESTIMANDS}
            for flip in cand["flips"]:
                if "error" not in flip:
                    keys[flip["estimand"]].add(_flip_key(flip))
            for estimand in ESTIMANDS:
                if keys[estimand] != ref_keys[estimand]:
                    raise ValueError(
                        f"seed {seed}, candidate {name}: "
                        f"{estimand} flip keys differ from the C0 "
                        f"manifest")


def _quantile_block(values):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return None
    q = np.quantile(values, QUANTILES)
    return {"n": int(values.size), "p50": float(q[0]),
            "p90": float(q[1]), "p99": float(q[2]),
            "max": float(values.max())}


def load_seed_files(directory):
    files = sorted(pathlib.Path(directory).glob("seed*.json"))
    return [json.loads(p.read_text()) for p in files]


def _flip_key(flip):
    return (flip["node"], flip["band"], flip["sign"])


def collect_flip_events(seed_results, name):
    """Per-seed flip records: response, material set, stop reasons."""
    events = {}
    for seed_result in seed_results:
        cand = seed_result["candidates"].get(name)
        if cand is None or "error" in cand["base"]:
            continue
        seed = seed_result["seed"]
        per_est = {e: {"rms_l": [], "rms_p": [], "material": set(),
                       "zero_both": 0, "n": 0, "errors": 0,
                       "stop_reasons": {}} for e in ESTIMANDS}
        for flip in cand["flips"]:
            bucket = per_est[flip["estimand"]]
            if "error" in flip:
                bucket["errors"] += 1
                continue
            bucket["rms_l"].append(flip["response_rms_l"])
            bucket["rms_p"].append(flip["response_rms_p"])
            bucket["n"] += 1
            reason = str(flip.get("stop_reason"))
            bucket["stop_reasons"][reason] = \
                bucket["stop_reasons"].get(reason, 0) + 1
            if flip["material"]:
                bucket["material"].add(_flip_key(flip))
            if (flip["response_rms_l"] == 0.0
                    and flip["response_rms_p"] == 0.0):
                bucket["zero_both"] += 1
        events[seed] = per_est
    return events


def aggregate_candidate(seed_results, name, c0_events=None):
    """Frozen per-candidate statistics over one seed ensemble."""
    events = collect_flip_events(seed_results, name)
    drift_l, drift_p, truth_l, truth_p = [], [], [], []
    jaccard, retention, seconds, refits = [], [], [], []
    cond, bbox, overshoot, ess, ridge = [], [], [], [], []
    quad_min, dw_p50, dw_min, n_down = [], [], [], []
    stop_reasons, base_errors, flip_errors = {}, 0, 0

    for seed_result in seed_results:
        cand = seed_result["candidates"].get(name)
        if cand is None:
            continue
        base = cand["base"]
        if "error" in base:
            base_errors += 1
            continue
        drift_l.append(base["drift_rms_l"])
        drift_p.append(base["drift_rms_p"])
        truth_l.append(base["truth_rms_l"])
        truth_p.append(base["truth_rms_p"])
        retention.append(base["retention"])
        seconds.append(base["seconds"])
        refits.append(base["refits"])
        if base.get("jaccard_vs_c0") is not None:
            jaccard.append(base["jaccard_vs_c0"])
        if base.get("normal_matrix_cond") is not None:
            cond.append(base["normal_matrix_cond"])
        coverage = base.get("spatial_coverage") or {}
        if coverage.get("bbox_area_frac") is not None:
            bbox.append(coverage["bbox_area_frac"])
        if coverage.get("quadrant_counts"):
            quad_min.append(min(coverage["quadrant_counts"]))
        if base.get("final_batch_overshoot") is not None:
            overshoot.append(base["final_batch_overshoot"])
        if base.get("ess_kish") is not None:
            ess.append(base["ess_kish"])
        downweight = base.get("downweight_quantiles")
        if downweight:
            dw_p50.append(downweight["p50"])
            dw_min.append(downweight["min"])
        if base.get("n_downweighted") is not None:
            n_down.append(base["n_downweighted"])
        if base.get("ridge_fallbacks") is not None:
            ridge.append(base["ridge_fallbacks"])
        reason = str(base.get("stop_reason"))
        stop_reasons[reason] = stop_reasons.get(reason, 0) + 1

    flip_stop_reasons = {}
    out_estimands = {}
    for estimand in ESTIMANDS:
        rms_l, rms_p = [], []
        n_flips = n_material = n_zero = 0
        per_seed_material = {}
        transitions = ({"persistent": 0, "resolved": 0,
                        "introduced": 0} if c0_events is not None
                       else None)
        for seed, per_est in events.items():
            bucket = per_est[estimand]
            flip_errors += bucket["errors"]
            rms_l.extend(bucket["rms_l"])
            rms_p.extend(bucket["rms_p"])
            n_flips += bucket["n"]
            n_material += len(bucket["material"])
            n_zero += bucket["zero_both"]
            per_seed_material[str(seed)] = len(bucket["material"])
            for reason, count in bucket["stop_reasons"].items():
                flip_stop_reasons[reason] = \
                    flip_stop_reasons.get(reason, 0) + count
            if c0_events is not None and seed in c0_events:
                ref = c0_events[seed][estimand]["material"]
                cur = bucket["material"]
                transitions["persistent"] += len(ref & cur)
                transitions["resolved"] += len(ref - cur)
                transitions["introduced"] += len(cur - ref)
        if n_flips == 0:
            out_estimands[estimand] = None
            continue
        out_estimands[estimand] = {
            "response_rms_l": _quantile_block(rms_l),
            "response_rms_p": _quantile_block(rms_p),
            "n_flips": n_flips,
            "n_material": n_material,
            "material_rate": n_material / n_flips,
            "n_exact_zero_both": n_zero,
            "per_seed_material": per_seed_material,
            "transitions_vs_c0": transitions,
        }

    return {"n_seeds": len(drift_l), "base_errors": base_errors,
            "flip_errors": flip_errors,
            "stop_reasons": stop_reasons,
            "flip_stop_reasons": flip_stop_reasons,
            "drift_rms_l": _quantile_block(drift_l),
            "drift_rms_p": _quantile_block(drift_p),
            "truth_rms_l": _quantile_block(truth_l),
            "truth_rms_p": _quantile_block(truth_p),
            "retention": _quantile_block(retention),
            "seconds": _quantile_block(seconds),
            "refits": _quantile_block(refits),
            "jaccard_vs_c0": _quantile_block(jaccard),
            "normal_matrix_cond": _quantile_block(cond),
            "bbox_area_frac": _quantile_block(bbox),
            "quadrant_min": _quantile_block(quad_min),
            "final_batch_overshoot": _quantile_block(overshoot),
            "ess_kish": _quantile_block(ess),
            "downweight_p50": _quantile_block(dw_p50),
            "downweight_min": _quantile_block(dw_min),
            "n_downweighted": _quantile_block(n_down),
            "ridge_fallbacks_total": int(sum(ridge)),
            "estimands": out_estimands}


def rate_diff_bootstrap(agg, c0, rng):
    """Seed-paired cluster bootstrap of material-rate differences.

    Pools flips within each resampled seed set; returns the observed
    difference and the percentile 95% CI, in percentage points, for
    each estimand and for uniform+stratified combined.
    """
    results = {}
    combos = {e: (e,) for e in ESTIMANDS}
    combos["combined"] = ("uniform", "stratified")
    for label, members in combos.items():
        counts = []
        for estimand in members:
            own = agg["estimands"].get(estimand)
            ref = c0["estimands"].get(estimand)
            if own is None or ref is None:
                counts = None
                break
            own_ps, ref_ps = (own["per_seed_material"],
                              ref["per_seed_material"])
            seeds = sorted(set(own_ps) & set(ref_ps))
            flips_per_seed = own["n_flips"] / max(len(own_ps), 1)
            counts.append((np.array([own_ps[s] for s in seeds]),
                           np.array([ref_ps[s] for s in seeds]),
                           flips_per_seed))
        if not counts:
            results[label] = None
            continue
        n_seeds = len(counts[0][0])
        total_flips = sum(c[2] for c in counts) * n_seeds
        own_all = sum(c[0].sum() for c in counts)
        ref_all = sum(c[1].sum() for c in counts)
        observed = (own_all - ref_all) / total_flips * 100.0
        idx = rng.integers(0, n_seeds, size=(BOOTSTRAP_B, n_seeds))
        boot = np.zeros(BOOTSTRAP_B)
        for own_counts, ref_counts, _ in counts:
            boot += (own_counts[idx].sum(axis=1)
                     - ref_counts[idx].sum(axis=1))
        boot = boot / total_flips * 100.0
        lo, hi = np.percentile(boot, [2.5, 97.5])
        results[label] = {"diff_pp": float(observed),
                          "ci95_pp": [float(lo), float(hi)],
                          "n_seeds": int(n_seeds),
                          "bootstrap_b": BOOTSTRAP_B}
    return results


def evaluate_gates(agg, c0, real40k, name):
    """Pre-registered decision gates (section 7) for one candidate."""
    if agg["n_seeds"] == 0:
        return {"eligible": False, "reason": "no successful seeds"}
    gates = {}

    def rate(block, estimand):
        est = block["estimands"].get(estimand)
        return est["material_rate"] if est else None

    r40 = (real40k or {}).get("candidates", {}).get(name)
    r40_c0 = (real40k or {}).get("candidates", {}).get("C0")
    flip40 = (r40 or {}).get("driver_flip") or {}
    base40 = (r40 or {}).get("base") or {}
    base40_c0 = (r40_c0 or {}).get("base") or {}

    ok = []
    for estimand in ("uniform", "stratified"):
        c_rate, ref = rate(agg, estimand), rate(c0, estimand)
        ok.append(c_rate is not None and ref is not None
                  and (ref == 0.0 and c_rate == 0.0
                       or ref > 0.0 and c_rate <= GATE_RATE_FACTOR * ref))
    driver40 = max(flip40.get("response_rms_l", float("inf")),
                   flip40.get("response_rms_p", float("inf")))
    gates["g1_tail_flip"] = bool(all(ok)
                                 and driver40 < MATERIAL_JUMP_RMS)

    med_l = agg["drift_rms_l"]["p50"]
    med_p = agg["drift_rms_p"]["p50"]
    drift40 = max(base40.get("drift_rms_l", float("inf")),
                  base40.get("drift_rms_p", float("inf")))
    gates["g2_drift"] = bool(med_l <= GATE_DRIFT_RMS
                             and med_p <= GATE_DRIFT_RMS
                             and drift40 <= GATE_DRIFT_RMS)

    gates["g3_truth"] = bool(
        agg["truth_rms_l"]["p50"]
        <= GATE_TRUTH_FACTOR * c0["truth_rms_l"]["p50"]
        and agg["truth_rms_p"]["p50"]
        <= GATE_TRUTH_FACTOR * c0["truth_rms_p"]["p50"])

    # g4: every termination — synthetic base and flip fits AND the
    # 40k case-study base and driver-flip fits — must be on the
    # candidate's healthy allow-list. Missing or unknown stop
    # reasons fail the gate (fail closed).
    healthy = HEALTHY_STOPS.get(name, DEFAULT_HEALTHY)
    seen = set(agg["stop_reasons"]) | set(agg["flip_stop_reasons"])
    if r40 is not None:
        seen.add(str(base40.get("stop_reason")))
        seen.add(str(flip40.get("stop_reason")))
    gates["g4_termination"] = bool(
        agg["base_errors"] == 0 and agg["flip_errors"] == 0
        and seen and seen <= healthy)

    runtime40 = base40.get("seconds")
    runtime40_c0 = base40_c0.get("seconds")
    gates["g5_runtime"] = bool(
        runtime40 is not None and runtime40_c0 is not None
        and runtime40 <= GATE_RUNTIME_FACTOR * runtime40_c0)

    gates["all_pass"] = all(gates[k] for k in
                            ("g1_tail_flip", "g2_drift", "g3_truth",
                             "g4_termination", "g5_runtime"))
    return gates


def provenance_distribution(seed_results, repo_root=None):
    """HEAD x dirty counts plus generating-input blob IDs per HEAD."""
    cells = {}
    for seed_result in seed_results:
        prov = seed_result.get("provenance") or {}
        head = str(prov.get("generator_commit"))
        dirty = bool(prov.get("worktree_dirty"))
        cells.setdefault(head, {"clean": 0, "dirty": 0})
        cells[head]["dirty" if dirty else "clean"] += 1
    blobs = {}
    root = repo_root or pathlib.Path(__file__).resolve().parent.parent
    for head in cells:
        try:
            out = subprocess.run(
                ["git", "ls-tree", head, "--"] + list(GENERATING_INPUTS),
                cwd=root, capture_output=True, text=True, check=True)
            blobs[head] = {line.split("\t")[1]: line.split()[2]
                           for line in out.stdout.splitlines()}
        except (OSError, subprocess.CalledProcessError, IndexError):
            blobs[head] = None
    # Fail closed: identity is asserted only when EVERY observed
    # HEAD resolves EVERY generating-input path and all blob maps
    # are equal. A single unresolved HEAD forfeits the claim.
    complete = [b for b in blobs.values()
                if b is not None
                and set(b) == set(GENERATING_INPUTS)]
    identical = bool(cells and len(complete) == len(cells)
                     and all(b == complete[0] for b in complete))
    return {"scope": "seed_records_only",
            "cells": cells,
            "generating_input_blobs": blobs,
            "generating_inputs_identical": identical,
            "n_dirty": sum(c["dirty"] for c in cells.values()),
            "dirty_paths_recorded": False}


def markdown_table(summary):
    """Compact frontier table for the report."""
    lines = ["| cand | material U | material S | driver p99 L "
             "| drift p50 L | truth p50 L | retention p50 "
             "| ESS p50 | gates |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, block in summary["candidates"].items():
        agg, gates = block["ensemble"], block.get("gates", {})
        if agg["n_seeds"] == 0:
            lines.append(f"| {name} | (no seeds) | | | | | | | |")
            continue
        est_u = agg["estimands"].get("uniform")
        est_s = agg["estimands"].get("stratified")
        est_d = agg["estimands"].get("driver")
        ess = agg.get("ess_kish")
        gate_str = ("PASS" if gates.get("all_pass") else
                    ",".join(k[:2] for k, v in gates.items()
                             if k.startswith("g") and not v) or "-")
        lines.append(
            f"| {name} "
            f"| {est_u['material_rate']:.4f} "
            f"| {est_s['material_rate']:.4f} "
            f"| {est_d['response_rms_l']['p99']:.2e} "
            f"| {agg['drift_rms_l']['p50']:.2e} "
            f"| {agg['truth_rms_l']['p50']:.2e} "
            f"| {agg['retention']['p50']:.3f} "
            + (f"| {ess['p50']:.1f} " if ess else "| - ")
            + f"| {gate_str} |")
    return "\n".join(lines)


def build_summary(confirmatory_dir, real40k_path=None,
                  robustness_dirs=None):
    seed_results = load_seed_files(confirmatory_dir)
    if not seed_results:
        raise SystemExit(f"no seed files in {confirmatory_dir}")
    validate_archive(seed_results)
    names = list(seed_results[0]["candidates"].keys())
    real40k = (json.loads(pathlib.Path(real40k_path).read_text())
               if real40k_path else None)
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    candidates = {}
    c0_events = collect_flip_events(seed_results, "C0")
    c0 = aggregate_candidate(seed_results, "C0")
    for name in names:
        agg = (c0 if name == "C0"
               else aggregate_candidate(seed_results, name,
                                        c0_events=c0_events))
        block = {"ensemble": agg}
        if name != "C0":
            block["rate_diff_vs_c0"] = rate_diff_bootstrap(agg, c0,
                                                           rng)
        block["gates"] = evaluate_gates(agg, c0, real40k, name)
        if real40k and name in real40k.get("candidates", {}):
            entry = real40k["candidates"][name]
            block["real40k"] = {
                "base": {k: entry.get("base", {}).get(k) for k in
                         ("stop_reason", "retention", "refits",
                          "seconds", "drift_rms_l", "drift_rms_p")},
                "driver_flip": entry.get("driver_flip"),
            }
        candidates[name] = block

    all_results = list(seed_results)
    summary = {
        "confirmatory_dir": str(confirmatory_dir),
        "n_seeds": len(seed_results),
        "seed_range": [seed_results[0]["seed"],
                       seed_results[-1]["seed"]],
        "material_jump_rms": MATERIAL_JUMP_RMS,
        "bootstrap": {"b": BOOTSTRAP_B, "seed": BOOTSTRAP_SEED},
        "candidates": candidates,
    }
    if robustness_dirs:
        summary["robustness"] = {}
        for cell, directory in robustness_dirs.items():
            cell_results = load_seed_files(directory)
            if not cell_results:
                continue
            validate_archive(cell_results)
            all_results.extend(cell_results)
            cell_c0_events = collect_flip_events(cell_results, "C0")
            summary["robustness"][cell] = {
                "n_seeds": len(cell_results),
                "candidates": {
                    name: aggregate_candidate(
                        cell_results, name,
                        c0_events=(None if name == "C0"
                                   else cell_c0_events))
                    for name in names},
            }
    summary["provenance_distribution"] = provenance_distribution(
        all_results)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True,
                        help="artifacts directory (contains "
                             "confirmatory/, robustness_*/, "
                             "real40k.json)")
    parser.add_argument("--out", required=True,
                        help="output summary JSON")
    parser.add_argument("--md", default=None,
                        help="optional markdown frontier table")
    args = parser.parse_args()

    root = pathlib.Path(args.dir)
    real40k = root / "real40k.json"
    robustness = {p.name.replace("robustness_", ""): p
                  for p in sorted(root.glob("robustness_*"))
                  if p.is_dir()}
    summary = build_summary(root / "confirmatory",
                            real40k if real40k.exists() else None,
                            robustness or None)
    pathlib.Path(args.out).write_text(
        json.dumps(summary, indent=1, sort_keys=True))
    print(f"wrote {args.out}", file=sys.stderr)
    if args.md:
        pathlib.Path(args.md).write_text(markdown_table(summary)
                                         + "\n")
        print(f"wrote {args.md}", file=sys.stderr)


if __name__ == "__main__":
    main()
