"""Tests of the mitigation-results aggregator (schema-level, no
upstream module required)."""

import json

import numpy as np
import pytest

from aggregate_mitigation_results import (aggregate_candidate,
                                          validate_archive,
                                          build_summary,
                                          collect_flip_events,
                                          evaluate_gates,
                                          markdown_table,
                                          provenance_distribution,
                                          rate_diff_bootstrap)


def _flip(estimand, node, rms, material, band="L", sign=-1,
          stop="w_test"):
    return {"estimand": estimand, "node": node, "band": band,
            "sign": sign, "response_rms_l": rms,
            "response_rms_p": rms / 2.0, "material": material,
            "stop_reason": stop, "n_removed": 10}


def _base(drift=0.0, truth=0.02, retention=0.05, seconds=1.0,
          refits=100, jaccard=1.0, stop="w_test", cond=50.0,
          bbox=0.9, ess=None):
    base = {"coefL": [0.0] * 6, "coefP": [0.0] * 6,
            "stop_reason": stop, "converged": stop == "w_test",
            "n_removed": 855, "retention": retention,
            "refits": refits, "seconds": seconds,
            "drift_rms_l": drift, "drift_rms_p": drift,
            "truth_rms_l": truth, "truth_rms_p": truth,
            "jaccard_vs_c0": jaccard, "inlier_ids": [1, 2, 3],
            "normal_matrix_cond": cond, "ridge_fallbacks": 0,
            "spatial_coverage": {"quadrant_counts": [1, 1, 1, 0],
                                 "bbox_area_frac": bbox},
            "final_batch_overshoot": 0}
    if ess is not None:
        base["ess_kish"] = ess
    return base


def _seed(seed, cands, commit="6350809ab", dirty=False):
    return {"seed": seed, "candidates": cands,
            "manifest_sha256": "0" * 64,
            "provenance": {"generator_commit": commit,
                           "worktree_dirty": dirty}}


def _make_ensemble():
    """Two seeds. C0: 2 material uniform flips (nodes 5, 6), 1
    stratified (node 15), driver material. GOOD: resolves all of
    them, introduces one uniform material at node 1 in seed 1001.
    BAD_DRIFT: stable but drifts beyond the gate."""
    seeds = []
    for seed in (1000, 1001):
        c0_flips = (
            [_flip("uniform", n, 0.0, False) for n in (1, 2, 3)]
            + [_flip("uniform", 5, 2e-2, True),
               _flip("uniform", 6, 3e-2, True)]
            + [_flip("stratified", n, 1e-5, False)
               for n in (11, 12, 13, 14)]
            + [_flip("stratified", 15, 3e-2, True)]
            + [_flip("driver", 99, 3.6e-2, True)])
        good_flips = (
            [_flip("uniform", n, 0.0, False) for n in (1, 2, 3, 5)]
            + [_flip("uniform", 6, 1e-5, False)]
            + [_flip("stratified", n, 1e-5, False)
               for n in (11, 12, 13, 14, 15)]
            + [_flip("driver", 99, 1e-4, False)])
        if seed == 1001:
            good_flips[0] = _flip("uniform", 1, 2e-2, True)
        seeds.append(_seed(seed, {
            "C0": {"base": _base(), "flips": c0_flips},
            "GOOD": {"base": _base(drift=1e-3, truth=0.02,
                                   jaccard=0.9),
                     "flips": good_flips},
            "BAD_DRIFT": {"base": _base(drift=5e-2, truth=0.02),
                          "flips": list(good_flips)},
        }, commit="6350809ab" if seed == 1000 else "ee802b2ff",
            dirty=(seed == 1001)))
    return seeds


REAL40K = {"candidates": {
    "C0": {"base": {"seconds": 100.0, "drift_rms_l": 0.0,
                    "drift_rms_p": 0.0, "stop_reason": "w_test",
                    "retention": 0.042, "refits": 38324},
           "driver_flip": {"response_rms_l": 3.6e-2,
                           "response_rms_p": 1e-5,
                           "material": True,
                           "stop_reason": "w_test"}},
    "GOOD": {"base": {"seconds": 120.0, "drift_rms_l": 1e-3,
                      "drift_rms_p": 1e-3, "stop_reason": "w_test",
                      "retention": 0.05, "refits": 9000},
             "driver_flip": {"response_rms_l": 1e-4,
                             "response_rms_p": 1e-5,
                             "material": False,
                             "stop_reason": "w_test"}},
    "BAD_DRIFT": {"base": {"seconds": 120.0, "drift_rms_l": 5e-2,
                           "drift_rms_p": 5e-2,
                           "stop_reason": "w_test",
                           "retention": 0.3, "refits": 5000},
                  "driver_flip": {"response_rms_l": 1e-4,
                                  "response_rms_p": 1e-5,
                                  "material": False,
                                  "stop_reason": "w_test"}},
}}


def test_aggregate_candidate_rates_and_quantiles():
    seeds = _make_ensemble()
    agg = aggregate_candidate(seeds, "C0")
    assert agg["n_seeds"] == 2
    uniform = agg["estimands"]["uniform"]
    assert uniform["n_flips"] == 10
    assert uniform["n_material"] == 4
    assert uniform["material_rate"] == pytest.approx(0.4)
    assert uniform["n_exact_zero_both"] == 6
    assert uniform["per_seed_material"] == {"1000": 2, "1001": 2}
    driver = agg["estimands"]["driver"]
    assert driver["material_rate"] == 1.0
    assert driver["response_rms_l"]["max"] == pytest.approx(3.6e-2)
    assert agg["stop_reasons"] == {"w_test": 2}
    assert agg["flip_stop_reasons"] == {"w_test": 22}
    # Pre-registered always-reported diagnostics survive aggregation.
    assert agg["normal_matrix_cond"]["p50"] == 50.0
    assert agg["bbox_area_frac"]["p50"] == pytest.approx(0.9)
    assert agg["final_batch_overshoot"]["max"] == 0.0
    assert agg["ess_kish"] is None


def test_paired_transitions():
    seeds = _make_ensemble()
    c0_events = collect_flip_events(seeds, "C0")
    good = aggregate_candidate(seeds, "GOOD", c0_events=c0_events)
    tr = good["estimands"]["uniform"]["transitions_vs_c0"]
    # C0 material: nodes 5,6 per seed (4 events); GOOD resolves all
    # of them and introduces node 1 in seed 1001.
    assert tr == {"persistent": 0, "resolved": 4, "introduced": 1}
    tr_d = good["estimands"]["driver"]["transitions_vs_c0"]
    assert tr_d == {"persistent": 0, "resolved": 2, "introduced": 0}


def test_rate_diff_bootstrap_deterministic():
    seeds = _make_ensemble()
    c0 = aggregate_candidate(seeds, "C0")
    good = aggregate_candidate(seeds, "GOOD")
    rng = np.random.default_rng(20260819)
    diffs = rate_diff_bootstrap(good, c0, rng)
    # uniform: GOOD 1/10 vs C0 4/10 -> -30 pp
    assert diffs["uniform"]["diff_pp"] == pytest.approx(-30.0)
    assert diffs["combined"]["diff_pp"] == pytest.approx(-25.0)
    lo, hi = diffs["uniform"]["ci95_pp"]
    assert lo <= diffs["uniform"]["diff_pp"] <= hi
    # Replayable: same seed, same interval.
    rng2 = np.random.default_rng(20260819)
    assert rate_diff_bootstrap(good, c0, rng2)["uniform"] \
        == diffs["uniform"]


def test_gates_pass_and_fail():
    seeds = _make_ensemble()
    c0 = aggregate_candidate(seeds, "C0")
    good = aggregate_candidate(seeds, "GOOD")
    bad = aggregate_candidate(seeds, "BAD_DRIFT")

    g_good = evaluate_gates(good, c0, REAL40K, "GOOD")
    # GOOD retains 1/4 of C0's uniform rate -> fails the 10x gate.
    assert not g_good["g1_tail_flip"]
    assert g_good["g2_drift"] and g_good["g3_truth"]
    assert g_good["g4_termination"] and g_good["g5_runtime"]

    g_bad = evaluate_gates(bad, c0, REAL40K, "BAD_DRIFT")
    assert not g_bad["g2_drift"]
    assert not g_bad["all_pass"]


def test_g4_covers_flip_stop_reasons():
    seeds = _make_ensemble()
    # Inject one flip ending by budget exhaustion.
    seeds[0]["candidates"]["GOOD"]["flips"][2] = _flip(
        "uniform", 3, 0.0, False, stop="max_refits")
    c0 = aggregate_candidate(seeds, "C0")
    good = aggregate_candidate(seeds, "GOOD")
    assert good["flip_stop_reasons"].get("max_refits") == 1
    gates = evaluate_gates(good, c0, REAL40K, "GOOD")
    assert not gates["g4_termination"]


def test_provenance_distribution():
    seeds = _make_ensemble()
    prov = provenance_distribution(seeds, repo_root="/nonexistent")
    assert prov["cells"] == {"6350809ab": {"clean": 1, "dirty": 0},
                             "ee802b2ff": {"clean": 0, "dirty": 1}}
    assert prov["n_dirty"] == 1
    assert prov["dirty_paths_recorded"] is False
    # Unresolvable commits -> no blob claim.
    assert prov["generating_inputs_identical"] is False


def test_build_summary_and_markdown(tmp_path):
    conf = tmp_path / "confirmatory"
    conf.mkdir()
    for result in _make_ensemble():
        (conf / f"seed{result['seed']:05d}.json").write_text(
            json.dumps(result))
    (tmp_path / "real40k.json").write_text(json.dumps(REAL40K))

    summary = build_summary(conf, tmp_path / "real40k.json")
    assert summary["n_seeds"] == 2
    good = summary["candidates"]["GOOD"]
    assert good["rate_diff_vs_c0"]["uniform"]["diff_pp"] \
        == pytest.approx(-30.0)
    assert good["ensemble"]["estimands"]["uniform"][
        "transitions_vs_c0"]["resolved"] == 4
    assert "rate_diff_vs_c0" not in summary["candidates"]["C0"]
    assert summary["provenance_distribution"]["n_dirty"] == 1

    table = markdown_table(summary)
    assert "| C0 " in table and "| GOOD " in table
    assert "ESS p50" in table
    # Material rates carry four decimals (0.021 vs 0.0215 class).
    assert "0.4000" in table

# ------------------------------------------------------------------
# Fail-closed archive validation and termination allow-lists

def test_validate_archive_accepts_complete_fixture():
    validate_archive(_make_ensemble())


def test_validate_archive_rejects_missing_candidate():
    seeds = _make_ensemble()
    del seeds[1]["candidates"]["GOOD"]
    with pytest.raises(ValueError, match="candidate set"):
        validate_archive(seeds)


def test_validate_archive_rejects_manifest_mismatch():
    seeds = _make_ensemble()
    seeds[0]["candidates"]["GOOD"]["flips"][0]["node"] = 777
    with pytest.raises(ValueError, match="flip keys differ"):
        validate_archive(seeds)


def test_validate_archive_rejects_missing_flip():
    seeds = _make_ensemble()
    seeds[0]["candidates"]["GOOD"]["flips"].pop(0)
    with pytest.raises(ValueError, match="flip keys differ"):
        validate_archive(seeds)


def test_validate_archive_requires_successful_c0():
    seeds = _make_ensemble()
    seeds[0]["candidates"]["C0"]["base"] = {"error": "boom"}
    with pytest.raises(ValueError, match="successful C0"):
        validate_archive(seeds)


def test_g4_rejects_budget_stop_and_unknown_and_missing():
    seeds = _make_ensemble()
    c0 = aggregate_candidate(seeds, "C0")
    # Known budget stop (IRLS) is never healthy for a w-test family.
    seeds_b = _make_ensemble()
    seeds_b[0]["candidates"]["GOOD"]["flips"][2]["stop_reason"] = \
        "irls_max_iterations"
    good_b = aggregate_candidate(seeds_b, "GOOD")
    assert not evaluate_gates(good_b, c0, REAL40K,
                              "GOOD")["g4_termination"]
    # Unknown stop reason fails closed.
    seeds_u = _make_ensemble()
    seeds_u[0]["candidates"]["GOOD"]["flips"][2]["stop_reason"] = \
        "mystery"
    good_u = aggregate_candidate(seeds_u, "GOOD")
    assert not evaluate_gates(good_u, c0, REAL40K,
                              "GOOD")["g4_termination"]
    # Missing real40k driver stop reason fails closed.
    import copy
    real = copy.deepcopy(REAL40K)
    del real["candidates"]["GOOD"]["driver_flip"]["stop_reason"]
    good = aggregate_candidate(seeds, "GOOD")
    assert not evaluate_gates(good, c0, real,
                              "GOOD")["g4_termination"]


def test_provenance_partial_resolution_fails_closed():
    # One resolvable HEAD plus one bogus HEAD must not assert blob
    # identity (fail closed on partial resolution).
    seeds = [_seed(1, {"C0": {"base": _base(), "flips": []}},
                   commit="HEAD"),
             _seed(2, {"C0": {"base": _base(), "flips": []}},
                   commit="0" * 40)]
    prov = provenance_distribution(seeds)
    assert prov["generating_input_blobs"]["HEAD"] is not None
    assert prov["generating_inputs_identical"] is False
    assert prov["scope"] == "seed_records_only"
