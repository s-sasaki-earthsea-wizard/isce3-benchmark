"""Aggregate the mitigation-ensemble outputs into the frozen report
statistics.

Consumes the per-seed JSONs written by ``run_mitigation_ensemble.py``
plus the ``real40k.json`` case study and produces, per candidate:

* flip-response distributions (p50/p90/p99/max per band) separately
  per estimand (uniform / stratified / driver), with the frozen
  material-jump exceedance rates and counts;
* unperturbed drift and truth-error distributions over seeds;
* termination-health, runtime, membership-Jaccard and retention
  summaries;
* the pre-registered decision-gate evaluation
  (``docs/polyfit-mitigation-prereg.md`` section 7) against the C0
  reference and the 40k case study.

Quantiles are the frozen report set; ranking statistics are the
pre-registered p99 and the material-jump exceedance rate — sample
maxima are reported, never ranked on. All statistics treat the seed
as the resampling cluster (rates are pooled over flips, but the
per-seed material counts are retained for cluster-aware intervals).
"""

import argparse
import json
import pathlib
import sys

import numpy as np

# Frozen constants (pre-registration sections 6-7).
MATERIAL_JUMP_RMS = 1e-2      # px
GATE_RATE_FACTOR = 0.1        # candidate rate <= 0.1 x C0 rate
GATE_DRIFT_RMS = 3.6e-2       # px, seed-median and 40k
GATE_TRUTH_FACTOR = 1.05      # x C0 seed-median
GATE_RUNTIME_FACTOR = 1.5     # x C0 on the 40k case
QUANTILES = (0.5, 0.9, 0.99)

ESTIMANDS = ("uniform", "stratified", "driver")


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


def aggregate_candidate(seed_results, name):
    """Frozen per-candidate statistics over one seed ensemble."""
    flips = {e: {"rms_l": [], "rms_p": [], "material": 0, "n": 0,
                 "per_seed_material": []} for e in ESTIMANDS}
    drift_l, drift_p, truth_l, truth_p = [], [], [], []
    jaccard, retention, seconds, refits = [], [], [], []
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
        reason = str(base.get("stop_reason"))
        stop_reasons[reason] = stop_reasons.get(reason, 0) + 1

        seed_material = {e: 0 for e in ESTIMANDS}
        for flip in cand["flips"]:
            if "error" in flip:
                flip_errors += 1
                continue
            bucket = flips[flip["estimand"]]
            bucket["rms_l"].append(flip["response_rms_l"])
            bucket["rms_p"].append(flip["response_rms_p"])
            bucket["n"] += 1
            if flip["material"]:
                bucket["material"] += 1
                seed_material[flip["estimand"]] += 1
        for estimand in ESTIMANDS:
            flips[estimand]["per_seed_material"].append(
                seed_material[estimand])

    out = {"n_seeds": len(drift_l), "base_errors": base_errors,
           "flip_errors": flip_errors, "stop_reasons": stop_reasons,
           "drift_rms_l": _quantile_block(drift_l),
           "drift_rms_p": _quantile_block(drift_p),
           "truth_rms_l": _quantile_block(truth_l),
           "truth_rms_p": _quantile_block(truth_p),
           "retention": _quantile_block(retention),
           "seconds": _quantile_block(seconds),
           "refits": _quantile_block(refits),
           "jaccard_vs_c0": _quantile_block(jaccard),
           "estimands": {}}
    for estimand in ESTIMANDS:
        bucket = flips[estimand]
        if bucket["n"] == 0:
            out["estimands"][estimand] = None
            continue
        out["estimands"][estimand] = {
            "response_rms_l": _quantile_block(bucket["rms_l"]),
            "response_rms_p": _quantile_block(bucket["rms_p"]),
            "n_flips": bucket["n"],
            "n_material": bucket["material"],
            "material_rate": bucket["material"] / bucket["n"],
            "per_seed_material": bucket["per_seed_material"],
        }
    return out


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

    reasons = agg["stop_reasons"]
    gates["g4_termination"] = bool(
        agg["base_errors"] == 0 and agg["flip_errors"] == 0
        and reasons.get("rank_failure", 0) == 0
        and reasons.get("max_refits", 0) == 0)

    runtime40 = base40.get("seconds")
    runtime40_c0 = base40_c0.get("seconds")
    gates["g5_runtime"] = bool(
        runtime40 is not None and runtime40_c0 is not None
        and runtime40 <= GATE_RUNTIME_FACTOR * runtime40_c0)

    gates["all_pass"] = all(gates[k] for k in
                            ("g1_tail_flip", "g2_drift", "g3_truth",
                             "g4_termination", "g5_runtime"))
    return gates


def markdown_table(summary):
    """Compact frontier table for the report."""
    lines = ["| cand | material U | material S | driver p99 L "
             "| drift p50 L | truth p50 L | retention p50 "
             "| gates |",
             "|---|---|---|---|---|---|---|---|"]
    for name, block in summary["candidates"].items():
        agg, gates = block["ensemble"], block.get("gates", {})
        if agg["n_seeds"] == 0:
            lines.append(f"| {name} | (no seeds) | | | | | | |")
            continue
        est_u = agg["estimands"].get("uniform")
        est_s = agg["estimands"].get("stratified")
        est_d = agg["estimands"].get("driver")
        gate_str = ("PASS" if gates.get("all_pass") else
                    ",".join(k[:2] for k, v in gates.items()
                             if k.startswith("g") and not v) or "-")
        lines.append(
            f"| {name} "
            f"| {est_u['material_rate']:.3f} "
            f"| {est_s['material_rate']:.3f} "
            f"| {est_d['response_rms_l']['p99']:.2e} "
            f"| {agg['drift_rms_l']['p50']:.2e} "
            f"| {agg['truth_rms_l']['p50']:.2e} "
            f"| {agg['retention']['p50']:.3f} "
            f"| {gate_str} |")
    return "\n".join(lines)


def build_summary(confirmatory_dir, real40k_path=None,
                  robustness_dirs=None):
    seed_results = load_seed_files(confirmatory_dir)
    if not seed_results:
        raise SystemExit(f"no seed files in {confirmatory_dir}")
    names = list(seed_results[0]["candidates"].keys())
    real40k = (json.loads(pathlib.Path(real40k_path).read_text())
               if real40k_path else None)

    candidates = {}
    c0 = aggregate_candidate(seed_results, "C0")
    for name in names:
        agg = (c0 if name == "C0"
               else aggregate_candidate(seed_results, name))
        block = {"ensemble": agg}
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

    summary = {
        "confirmatory_dir": str(confirmatory_dir),
        "n_seeds": len(seed_results),
        "seed_range": [seed_results[0]["seed"],
                       seed_results[-1]["seed"]],
        "material_jump_rms": MATERIAL_JUMP_RMS,
        "candidates": candidates,
        "provenance_sample": seed_results[0].get("provenance"),
    }
    if robustness_dirs:
        summary["robustness"] = {}
        for cell, directory in robustness_dirs.items():
            cell_results = load_seed_files(directory)
            if not cell_results:
                continue
            summary["robustness"][cell] = {
                "n_seeds": len(cell_results),
                "candidates": {
                    name: aggregate_candidate(cell_results, name)
                    for name in names},
            }
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
