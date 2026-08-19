"""Tests of the polyfit sensitivity harness.

The upstream isce3 ``offsets_polyfit`` module is imported from the
installed package or the source checkout (``ISCE3_PY_SRC``); the whole
module is skipped when neither is available.
"""

import numpy as np
import pytest

from polyfit_sensitivity import (coef_delta_stats, first_divergence,
                                 load_offsets_polyfit,
                                 make_pure_synthetic, polyfit_traced,
                                 production_fit_kwargs)

try:
    op = load_offsets_polyfit()
except ImportError:
    op = None

pytestmark = pytest.mark.skipif(
    op is None, reason="upstream offsets_polyfit not importable")


@pytest.fixture(scope="module")
def small_case():
    """A small synthetic dataset that forces several removals."""
    data, _ = make_pure_synthetic(n_az=12, n_rg=12, noise_std=0.02,
                                  seed=3)
    # Plant two strong outliers so the removal loop has real work.
    data[20, 3] += 40.0
    data[77, 4] -= 25.0
    return data


def test_mirror_equivalence_bit_identical(small_case):
    """The traced mirror must reproduce upstream exactly."""
    kwargs = production_fit_kwargs()
    upstream = op.polyfit_offsets(small_case.copy(),
                                  max_iterations=len(small_case),
                                  **kwargs)
    mirrored, trace = polyfit_traced(op, small_case, **kwargs)
    np.testing.assert_array_equal(upstream["coefL"],
                                  mirrored["coefL"])
    np.testing.assert_array_equal(upstream["coefP"],
                                  mirrored["coefP"])
    assert upstream["removed_indices"] == mirrored["removed_indices"]
    assert len(trace) == len(mirrored["removed_indices"]) + 1


def test_aa_determinism(small_case):
    kwargs = production_fit_kwargs()
    r1 = op.polyfit_offsets(small_case.copy(),
                            max_iterations=len(small_case), **kwargs)
    r2 = op.polyfit_offsets(small_case.copy(),
                            max_iterations=len(small_case), **kwargs)
    np.testing.assert_array_equal(r1["coefL"], r2["coefL"])
    np.testing.assert_array_equal(r1["coefP"], r2["coefP"])
    assert r1["removed_indices"] == r2["removed_indices"]


def test_noiseless_recovery():
    """Exact quadratic input: immediate stop, coefficients recovered."""
    coef_l = (0.4, 1.2, -0.6, 0.3, -0.2, 0.15)
    coef_p = (-0.2, 0.5, 0.8, -0.25, 0.1, -0.3)
    data, _ = make_pure_synthetic(n_az=10, n_rg=10, noise_std=0.0,
                                  quantize=False, seed=1,
                                  coef_l=coef_l, coef_p=coef_p)
    res, trace = polyfit_traced(op, data, **production_fit_kwargs())
    assert res["removed_indices"] == []
    assert len(trace) == 1
    # float32 storage of the offsets bounds the recovery accuracy
    np.testing.assert_allclose(res["coefL"], coef_l, atol=1e-5)
    np.testing.assert_allclose(res["coefP"], coef_p, atol=1e-5)


def test_planted_outlier_removed_first(small_case):
    res, trace = polyfit_traced(op, small_case,
                                **production_fit_kwargs())
    # The two planted outliers dominate the combined score and must
    # lead the removal sequence.
    assert set(res["removed_indices"][:2]) == {20, 77}
    # Their removal iterations carry huge stop margins (w-score
    # regime, orders above crit_value).
    assert trace[0]["stop_margin"] > 10.0


def test_first_divergence():
    assert first_divergence([1, 2, 3], [1, 2, 3]) is None
    assert first_divergence([1, 2, 3], [1, 5, 3]) == 1
    assert first_divergence([1, 2], [1, 2, 3]) == 2
    assert first_divergence([], []) is None


def test_coef_delta_stats_zero():
    stats = coef_delta_stats(op, np.zeros(6), np.zeros(6))
    assert stats["l"]["max_abs"] == 0.0
    assert stats["p"]["rms"] == 0.0


def test_coef_delta_stats_constant_shift():
    dcoef = np.zeros(6)
    dcoef[0] = 0.01  # constant term only
    stats = coef_delta_stats(op, dcoef, np.zeros(6))
    assert stats["l"]["mean"] == pytest.approx(0.01)
    assert stats["l"]["peak_to_peak"] == pytest.approx(0.0, abs=1e-12)
    assert stats["p"]["max_abs"] == 0.0


# ---------------------------------------------------------------------
# replay-scenario helpers
# ---------------------------------------------------------------------

from polyfit_sensitivity import (build_replay_coordinates,  # noqa: E402
                                 coef_vector_judgment,
                                 extract_replay_samples,
                                 induced_rms_curves,
                                 make_factorial_data, open_envi_band,
                                 parse_logged_coefs,
                                 sample_id_for_node)


def _write_envi(path, array, interleave="bip"):
    """Write a (lines, samples[, bands]) array as a flat ENVI raster."""
    array = np.asarray(array)
    if array.ndim == 2:
        array = array[:, :, None]
    lines, samples, bands = array.shape
    dtype_code = {np.dtype("float32"): 4,
                  np.dtype("float64"): 5}[array.dtype]
    if interleave == "bip":
        array.tofile(path)
    elif interleave == "bsq":
        np.moveaxis(array, 2, 0).copy().tofile(path)
    else:
        raise ValueError(interleave)
    path.with_suffix(".hdr").write_text(
        "ENVI\n"
        f"samples = {samples}\nlines   = {lines}\nbands   = {bands}\n"
        "header offset = 0\nfile type = ENVI Standard\n"
        f"data type = {dtype_code}\ninterleave = {interleave}\n"
        "byte order = 0\n")


def test_open_envi_band_bip_and_bsq(tmp_path):
    # open_envi_band delegates ENVI-header parsing to compare_slc,
    # which lives in the sibling nisar-displacement repo and is not
    # ported here (only the replay arena needs it).
    pytest.importorskip("compare_slc")
    rng = np.random.default_rng(0)
    cube = rng.normal(size=(4, 5, 2)).astype(np.float32)
    bip = tmp_path / "cube_bip"
    _write_envi(bip, cube, "bip")
    np.testing.assert_array_equal(open_envi_band(bip, 1), cube[:, :, 0])
    np.testing.assert_array_equal(open_envi_band(bip, 2), cube[:, :, 1])
    flat = rng.normal(size=(3, 7)).astype(np.float64)
    bsq = tmp_path / "flat_bsq"
    _write_envi(bsq, flat, "bsq")
    np.testing.assert_array_equal(open_envi_band(bsq), flat)


def test_parse_logged_coefs(tmp_path):
    log = tmp_path / "insar.log"
    log.write_text(
        "journal (rubbersheet.run_rubbersheet_with_polyfit):\n"
        " -- Polyfitting CoefL: [-0.14150877  0.37200424  0.27663526\n"
        " -0.1895345  -0.34199518 -0.0197245 ]\n"
        " -- Polyfitting CoefP: [-0.60432333  0.15501332  0.11391908"
        " -0.09144216  0.12697729 -0.11241049]\n")
    coefs = parse_logged_coefs(log)
    assert coefs["coefL"][0] == pytest.approx(-0.14150877)
    assert coefs["coefL"][3] == pytest.approx(-0.1895345)
    assert len(coefs["coefL"]) == len(coefs["coefP"]) == 6


def _tiny_coords():
    """Handmade coordinates covering a 4x6 offsets grid, 3x4 samples."""
    off_az = np.array([10, 20, 30, 40])
    off_rg = np.array([5, 15, 25, 35, 45, 55])
    az_sampled = np.linspace(0, 3, 3, dtype=int)
    rg_sampled = np.linspace(0, 5, 4, dtype=int)
    return {
        "off_az_indices": off_az, "off_rg_indices": off_rg,
        "az_sampled": az_sampled, "rg_sampled": rg_sampled,
        "az_polyfit_indices": off_az[az_sampled],
        "rg_polyfit_indices": off_rg[rg_sampled],
    }


def test_extract_replay_samples(tmp_path):
    pytest.importorskip("compare_slc")  # see test_open_envi_band_*
    coords = _tiny_coords()
    rng = np.random.default_rng(1)
    dense = rng.normal(size=(4, 6, 2)).astype(np.float32)
    peak = rng.uniform(0.01, 1.0, size=(4, 6)).astype(np.float32)
    _write_envi(tmp_path / "dense_offsets", dense, "bip")
    _write_envi(tmp_path / "correlation_peak", peak, "bip")
    data = extract_replay_samples(tmp_path, coords)
    assert data.shape == (12, 6)
    assert data.dtype == np.float64
    # Row order is azimuth-major; check a middle row end to end.
    row = 1 * 4 + 2  # az position 1, rg position 2
    az_i, rg_i = coords["az_sampled"][1], coords["rg_sampled"][2]
    assert data[row, 0] == row
    assert data[row, 1] == coords["off_az_indices"][az_i]
    assert data[row, 2] == coords["off_rg_indices"][rg_i]
    assert data[row, 3] == np.float64(dense[az_i, rg_i, 0])
    assert data[row, 4] == np.float64(dense[az_i, rg_i, 1])
    assert data[row, 5] == np.float64(peak[az_i, rg_i])


def test_build_replay_coordinates(tmp_path):
    h5py = pytest.importorskip("h5py")
    prf, rg_spacing = 1520.0, 3.0
    az_t0, sr0 = 1000.0, 800000.0
    n_lines, n_pixels = 100, 120
    rslc = tmp_path / "rslc.h5"
    with h5py.File(rslc, "w") as f:
        sw = f.create_group("science/LSAR/RSLC/swaths")
        sw["zeroDopplerTime"] = az_t0 + np.arange(n_lines) / prf
        sw["zeroDopplerTimeSpacing"] = 1.0 / prf
        fa = sw.create_group("frequencyA")
        fa["slantRange"] = sr0 + np.arange(n_pixels) * rg_spacing
        fa["slantRangeSpacing"] = rg_spacing
        fa["processedAzimuthBandwidth"] = 1000.0
        fa["processedRangeBandwidth"] = 40e6
    rifg = tmp_path / "rifg.h5"
    # Offsets grid: every 10th line from 5, every 20th pixel from 8.
    off_lines = np.arange(5, 96, 10)
    off_pixels = np.arange(8, 109, 20)
    with h5py.File(rifg, "w") as f:
        po = f.create_group(
            "science/LSAR/RIFG/swaths/frequencyA/pixelOffsets")
        po["zeroDopplerTime"] = az_t0 + off_lines / prf
        po["slantRange"] = sr0 + off_pixels * rg_spacing
    coords = build_replay_coordinates(rifg, rslc, n_az=5, n_rg=4)
    np.testing.assert_array_equal(coords["off_az_indices"], off_lines)
    np.testing.assert_array_equal(coords["off_rg_indices"],
                                  off_pixels)
    np.testing.assert_array_equal(
        coords["az_sampled"], np.linspace(0, 9, 5, dtype=int))
    kw = coords["fit_kwargs"]
    assert kw["maxL"] == n_lines and kw["maxP"] == n_pixels
    assert kw["prf"] == pytest.approx(prf)
    assert kw["rsr"] == pytest.approx(299792458.0 / rg_spacing * 0.5)


def test_make_factorial_data():
    base = np.arange(24, dtype=float).reshape(4, 6)
    donor = base + 100.0
    off = make_factorial_data(base, donor, "offsets")
    np.testing.assert_array_equal(off[:, [3, 4]], donor[:, [3, 4]])
    np.testing.assert_array_equal(off[:, [0, 1, 2, 5]],
                                  base[:, [0, 1, 2, 5]])
    w = make_factorial_data(base, donor, "weights")
    np.testing.assert_array_equal(w[:, 5], donor[:, 5])
    np.testing.assert_array_equal(w[:, :5], base[:, :5])
    # The inputs must not be mutated.
    np.testing.assert_array_equal(base,
                                  np.arange(24, dtype=float)
                                  .reshape(4, 6))


def test_sample_id_for_node():
    coords = _tiny_coords()
    # az position 1 (raw line 1), rg position 2 (raw sample 3).
    assert sample_id_for_node(coords, 1, 3) == 1 * 4 + 2
    assert sample_id_for_node(coords, 2, 3) is None


def test_coef_log_records_trajectory(small_case):
    coef_log = []
    res, trace = polyfit_traced(op, small_case, coef_log=coef_log,
                                **production_fit_kwargs())
    assert len(coef_log) == len(trace)
    np.testing.assert_array_equal(coef_log[-1][0], res["coefL"])
    np.testing.assert_array_equal(coef_log[-1][1], res["coefP"])


def test_coef_vector_judgment_identity_and_orthogonal():
    t_l, t_p = np.arange(1.0, 7.0), np.arange(-6.0, 0.0)
    same = coef_vector_judgment(t_l, t_p, t_l, t_p)
    assert same["all"]["cosine"] == pytest.approx(1.0)
    assert same["all"]["norm_ratio"] == pytest.approx(1.0)
    zero = coef_vector_judgment(np.zeros(6), np.zeros(6), t_l, t_p)
    assert zero["all"]["cosine"] is None
    assert zero["all"]["norm_ratio"] == pytest.approx(0.0)


# ---------------------------------------------------------------------
# minrepro scenario
# ---------------------------------------------------------------------

from polyfit_sensitivity import (MIN_REPRO_DRIVER_PEAK,  # noqa: E402
                                 MIN_REPRO_DRIVER_RADAR,
                                 MIN_REPRO_SEED, OFFSET_QUANTUM,
                                 judge_min_repro,
                                 make_min_repro_case,
                                 parse_number_list, probe_judge)


def test_min_repro_case_structure():
    data, info = make_min_repro_case(seed=0)
    assert data.shape == (900, 6)
    drv = info["driver_id"]
    # The driver sits at the grid node nearest the real radar node
    # and carries the real correlation peak (through float32).
    assert abs(info["driver_node"]["line"]
               - MIN_REPRO_DRIVER_RADAR[0]) <= 41040 / 29 / 2
    assert abs(info["driver_node"]["pixel"]
               - MIN_REPRO_DRIVER_RADAR[1]) <= 52906 / 29 / 2
    assert data[drv, 1] == info["driver_node"]["line"]
    assert data[drv, 2] == info["driver_node"]["pixel"]
    assert data[drv, 5] == pytest.approx(MIN_REPRO_DRIVER_PEAK,
                                         abs=1e-7)
    # All offsets sit on the 1/32 px grid (float32 storage bounds
    # the residual of the grid snap).
    for col in (3, 4):
        snapped = np.round(data[:, col] / OFFSET_QUANTUM)
        assert np.abs(data[:, col]
                      - snapped * OFFSET_QUANTUM).max() < 1e-5


def test_min_repro_case_deterministic():
    d1, i1 = make_min_repro_case(seed=7)
    d2, i2 = make_min_repro_case(seed=7)
    np.testing.assert_array_equal(d1, d2)
    assert i1 == i2
    d3, _ = make_min_repro_case(seed=8)
    assert not np.array_equal(d1, d3)


def test_production_sensor_matches_replay_provenance():
    """PROD_SENSOR must stay pinned to the workflow-call values.

    The authoritative values are the ones build_replay_coordinates
    rebuilds from the RIFG/RSLC files (recorded in the replay JSON
    provenance); the sigmas below are copied from that record.
    """
    from polyfit_sensitivity import PROD_SENSOR
    sigma_l = 0.15 / (PROD_SENSOR["prf"] / PROD_SENSOR["abw"])
    sigma_p = 0.10 / (PROD_SENSOR["rsr"] / PROD_SENSOR["rbw"])
    assert sigma_l == pytest.approx(0.12470527678472171, abs=1e-16)
    assert sigma_p == pytest.approx(0.08333333333333334, abs=1e-16)
    assert PROD_SENSOR["rsr"] == 48000000.0


def test_min_repro_pinned_seed_passes():
    """The pinned existence proof must satisfy every criteria check."""
    data, info = make_min_repro_case(seed=MIN_REPRO_SEED)
    rec = judge_min_repro(op, data, info["driver_id"],
                          production_fit_kwargs())
    assert rec["passed"], rec["checks"]
    assert "flip_removal_in_endgame" in rec["checks"]
    flip = rec["flip"]
    # The flip is an endgame phenomenon: the driver is removed in the
    # last ~5% of the removal chain, and the final membership changes
    # beyond the driver itself.
    assert flip["driver_removal_chain_fraction"] > 0.9
    assert flip["membership_delta_beyond_driver"] >= 1
    assert flip["induced_offset"]["l"]["rms"] >= 0.01


def test_parse_number_list():
    assert parse_number_list("-1/32,-1e-4,0.5") == [
        -1.0 / 32.0, -1e-4, 0.5]
    assert parse_number_list("-2, 1, 2") == [-2.0, 1.0, 2.0]
    assert parse_number_list("") == []


def test_probe_judge_identity():
    """probe_judge on identical fits reports a null response."""
    data, info = make_min_repro_case(seed=0)
    kwargs = production_fit_kwargs()
    res, _ = polyfit_traced(op, data, **kwargs)
    target = np.arange(1.0, 7.0)
    rec = probe_judge(op, res, res, target, target)
    assert rec["induced_rms_l"] == 0.0
    assert rec["first_divergence"] is None
    assert rec["judgment_vs_target"]["all"]["norm_ratio"] == 0.0


def test_induced_rms_curves_constant_offset():
    kwargs = production_fit_kwargs()
    zero = np.zeros(6)
    shift = np.zeros(6)
    shift[0] = 0.5
    log_a = [(shift.copy(), zero.copy()), (shift.copy(), zero.copy())]
    log_b = [(zero.copy(), zero.copy())] * 3
    curves = induced_rms_curves(op, log_a, log_b, kwargs)
    # Aligned by iteration: min length wins.
    assert curves["rms_l"].shape == (2,)
    np.testing.assert_allclose(curves["rms_l"], 0.5, rtol=1e-12)
    np.testing.assert_allclose(curves["rms_p"], 0.0, atol=1e-15)
