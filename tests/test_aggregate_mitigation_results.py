"""Tests of the mitigation-results aggregator (schema-level, no
upstream module required)."""

import json

import pytest

from aggregate_mitigation_results import (aggregate_candidate,
                                          build_summary,
                                          evaluate_gates,
                                          markdown_table)


def _flip(estimand, rms, material, band="L", node=1, sign=-1):
    return {"estimand": estimand, "node": node, "band": band,
            "sign": sign, "response_rms_l": rms,
            "response_rms_p": rms / 2.0, "material": material,
            "stop_reason": "w_test", "n_removed": 10}


def _base(drift=0.0, truth=0.02, retention=0.05, seconds=1.0,
          refits=100, jaccard=1.0, stop="w_test"):
    return {"coefL": [0.0] * 6, "coefP": [0.0] * 6,
            "stop_reason": stop, "converged": stop == "w_test",
            "n_removed": 855, "retention": retention,
            "refits": refits, "seconds": seconds,
            "drift_rms_l": drift, "drift_rms_p": drift,
            "truth_rms_l": truth, "truth_rms_p": truth,
            "jaccard_vs_c0": jaccard, "inlier_ids": [1, 2, 3]}


def _seed(seed, cands):
    return {"seed": seed, "candidates": cands,
            "manifest_sha256": "0" * 64}


def _make_ensemble():
    """Two seeds: C0 jumps on the driver flip, GOOD never jumps,
    BAD_DRIFT is stable but drifts beyond the gate."""
    seeds = []
    for seed in (1000, 1001):
        c0_flips = ([_flip("uniform", 1e-5, False)] * 4
                    + [_flip("uniform", 2e-2, True)]
                    + [_flip("stratified", 1e-5, False)] * 4
                    + [_flip("stratified", 3e-2, True)]
                    + [_flip("driver", 3.6e-2, True)])
        good_flips = ([_flip("uniform", 1e-5, False)] * 5
                      + [_flip("stratified", 1e-5, False)] * 5
                      + [_flip("driver", 1e-4, False)])
        bad_flips = list(good_flips)
        seeds.append(_seed(seed, {
            "C0": {"base": _base(), "flips": c0_flips},
            "GOOD": {"base": _base(drift=1e-3, truth=0.02,
                                   jaccard=0.9),
                     "flips": good_flips},
            "BAD_DRIFT": {"base": _base(drift=5e-2, truth=0.02),
                          "flips": bad_flips},
        }))
    return seeds


REAL40K = {"candidates": {
    "C0": {"base": {"seconds": 100.0, "drift_rms_l": 0.0,
                    "drift_rms_p": 0.0, "stop_reason": "w_test",
                    "retention": 0.042, "refits": 38324},
           "driver_flip": {"response_rms_l": 3.6e-2,
                           "response_rms_p": 1e-5,
                           "material": True}},
    "GOOD": {"base": {"seconds": 120.0, "drift_rms_l": 1e-3,
                      "drift_rms_p": 1e-3, "stop_reason": "w_test",
                      "retention": 0.05, "refits": 9000},
             "driver_flip": {"response_rms_l": 1e-4,
                             "response_rms_p": 1e-5,
                             "material": False}},
    "BAD_DRIFT": {"base": {"seconds": 120.0, "drift_rms_l": 5e-2,
                           "drift_rms_p": 5e-2,
                           "stop_reason": "w_test",
                           "retention": 0.3, "refits": 5000},
                  "driver_flip": {"response_rms_l": 1e-4,
                                  "response_rms_p": 1e-5,
                                  "material": False}},
}}


def test_aggregate_candidate_rates_and_quantiles():
    agg = aggregate_candidate(_make_ensemble(), "C0")
    assert agg["n_seeds"] == 2
    uniform = agg["estimands"]["uniform"]
    assert uniform["n_flips"] == 10
    assert uniform["n_material"] == 2
    assert uniform["material_rate"] == pytest.approx(0.2)
    assert uniform["per_seed_material"] == [1, 1]
    driver = agg["estimands"]["driver"]
    assert driver["material_rate"] == 1.0
    assert driver["response_rms_l"]["max"] == pytest.approx(3.6e-2)
    assert agg["drift_rms_l"]["p50"] == 0.0
    assert agg["stop_reasons"] == {"w_test": 2}


def test_gates_pass_and_fail():
    seeds = _make_ensemble()
    c0 = aggregate_candidate(seeds, "C0")
    good = aggregate_candidate(seeds, "GOOD")
    bad = aggregate_candidate(seeds, "BAD_DRIFT")

    g_good = evaluate_gates(good, c0, REAL40K, "GOOD")
    assert g_good["g1_tail_flip"]
    assert g_good["g2_drift"]
    assert g_good["g3_truth"]
    assert g_good["g4_termination"]
    assert g_good["g5_runtime"]
    assert g_good["all_pass"]

    g_bad = evaluate_gates(bad, c0, REAL40K, "BAD_DRIFT")
    assert g_bad["g1_tail_flip"]
    assert not g_bad["g2_drift"]
    assert not g_bad["all_pass"]

    # C0 trivially fails its own tail gate (rate not reduced 10x).
    g_c0 = evaluate_gates(c0, c0, REAL40K, "C0")
    assert not g_c0["g1_tail_flip"]


def test_build_summary_and_markdown(tmp_path):
    conf = tmp_path / "confirmatory"
    conf.mkdir()
    for result in _make_ensemble():
        (conf / f"seed{result['seed']:05d}.json").write_text(
            json.dumps(result))
    (tmp_path / "real40k.json").write_text(json.dumps(REAL40K))

    summary = build_summary(conf, tmp_path / "real40k.json")
    assert summary["n_seeds"] == 2
    assert summary["candidates"]["GOOD"]["gates"]["all_pass"]
    assert summary["candidates"]["GOOD"]["real40k"]["driver_flip"][
        "response_rms_l"] == pytest.approx(1e-4)

    table = markdown_table(summary)
    assert "| C0 " in table and "| GOOD " in table
    assert "PASS" in table
    # C0 fails g1 (its own reference) and shows the failing gates.
    c0_row = [ln for ln in table.splitlines()
              if ln.startswith("| C0 ")][0]
    assert "PASS" not in c0_row