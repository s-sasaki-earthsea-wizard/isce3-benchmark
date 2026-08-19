"""Tests of the mitigation ensemble runner.

Small-grid cases (12x12) keep the fits fast; the pre-registered
production shapes are exercised only in the real runs. Skipped when
the upstream module is not importable.
"""

import json

import numpy as np
import pytest

from polyfit_sensitivity import (load_offsets_polyfit,
                                 make_min_repro_case)
from run_mitigation_ensemble import (EVAL_CANDIDATES,
                                     MATERIAL_JUMP_RMS,
                                     ROBUSTNESS_CELLS,
                                     _run_one_seed, eval_grid_design,
                                     evaluate_case, provenance,
                                     surface_rms)

try:
    op = load_offsets_polyfit()
except ImportError:
    op = None

pytestmark = pytest.mark.skipif(
    op is None, reason="upstream offsets_polyfit not importable")

SMALL = {"n_az": 12, "n_rg": 12}


def test_surface_rms_constant_coef():
    design = eval_grid_design(op)
    dcoef = np.zeros(6)
    dcoef[0] = 0.02  # constant surface offset
    rms_l, rms_p = surface_rms(design, dcoef, np.zeros(6))
    assert rms_l == pytest.approx(0.02)
    assert rms_p == 0.0


def test_evaluate_case_structure():
    out = evaluate_case(555, gen_kwargs=SMALL,
                        candidates=("C0", "C3", "C6"),
                        n_uniform=2, n_stratified=2)
    assert out["seed"] == 555
    assert set(out["candidates"]) == {"C0", "C3", "C6"}
    assert len(out["manifest_sha256"]) == 64
    c0 = out["candidates"]["C0"]
    # C0 is its own drift reference and Jaccard reference.
    assert c0["base"]["drift_rms_l"] == 0.0
    assert c0["base"]["jaccard_vs_c0"] == 1.0
    assert c0["base"]["truth_rms_l"] > 0.0
    # 2 + 2 manifest flips + the driver flip.
    assert len(c0["flips"]) == 5
    driver = [f for f in c0["flips"] if f["estimand"] == "driver"]
    assert len(driver) == 1
    assert driver[0]["band"] == "L" and driver[0]["sign"] == -1
    for f in c0["flips"]:
        assert isinstance(f["material"], bool)
        assert f["response_rms_l"] >= 0.0
    c3 = out["candidates"]["C3"]
    assert c3["base"]["drift_rms_l"] >= 0.0
    assert c3["base"]["jaccard_vs_c0"] is not None
    # C6 keeps everything: retention 1, membership defined (all ids).
    c6 = out["candidates"]["C6"]
    assert c6["base"]["retention"] == 1.0
    assert len(c6["base"]["inlier_ids"]) == 144


def test_evaluate_case_c5_membership_undefined():
    out = evaluate_case(555, gen_kwargs=SMALL, candidates=("C0", "C5"),
                        n_uniform=2, n_stratified=2)
    c5 = out["candidates"]["C5"]["base"]
    assert c5["inlier_ids"] is None
    assert c5["jaccard_vs_c0"] is None
    assert "ess_kish" in c5


def test_evaluate_case_puts_c0_first():
    out = evaluate_case(556, gen_kwargs=SMALL, candidates=("C3",),
                        n_uniform=2, n_stratified=2)
    assert list(out["candidates"]) == ["C0", "C3"]


def test_run_one_seed_writes_and_skips(tmp_path):
    msg = _run_one_seed((557, SMALL, str(tmp_path), ("C0",)))
    assert "done" in msg
    path = tmp_path / "seed00557.json"
    data = json.loads(path.read_text())
    assert data["seed"] == 557
    assert data["provenance"]["material_jump_rms"] == MATERIAL_JUMP_RMS
    assert data["provenance"]["numpy"] == np.__version__
    # Resume semantics: an existing file is never recomputed.
    msg2 = _run_one_seed((557, SMALL, str(tmp_path), ("C0",)))
    assert "skipped" in msg2


def test_robustness_cells_change_generator():
    base_data, base_info = make_min_repro_case(seed=558, **SMALL)
    corner_kwargs = dict(SMALL, **ROBUSTNESS_CELLS["driver_corner"])
    _, corner_info = make_min_repro_case(seed=558, **corner_kwargs)
    assert corner_info["driver_id"] != base_info["driver_id"]
    assert corner_info["driver_id"] == 12 * 12 - 1

    elite_kwargs = dict(SMALL, **ROBUSTNESS_CELLS["elite_high"])
    _, elite_info = make_min_repro_case(seed=558, **elite_kwargs)
    assert elite_info["n_coherent"] > base_info["n_coherent"]

    phase_kwargs = dict(SMALL, **ROBUSTNESS_CELLS["quant_phase"])
    phase_data, _ = make_min_repro_case(seed=558, **phase_kwargs)
    # Same draws, shifted quantizer: offsets sit on the shifted grid.
    q = 1.0 / 32.0
    residue = np.mod(phase_data[:, 3] - q / 2.0, q)
    residue = np.minimum(residue, q - residue)
    assert np.all(residue < 1e-6)
    assert not np.array_equal(phase_data[:, 3], base_data[:, 3])


def test_default_roster_frozen():
    assert EVAL_CANDIDATES == ("C0", "C2b", "C3", "C4", "C4b",
                               "C4+C3", "C5", "C6")
    assert set(ROBUSTNESS_CELLS) == {"quant_phase", "driver_corner",
                                     "elite_low", "elite_high"}


def test_provenance_block():
    prov = provenance(op)
    assert prov["eval_grid_n"] == 101
    assert "thread_env" in prov
    assert prov["upstream_module"]