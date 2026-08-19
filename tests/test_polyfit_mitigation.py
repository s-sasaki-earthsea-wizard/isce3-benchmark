"""Tests of the policy-aware mitigation mirror.

The equivalence tests here are a Phase-A deliverable of
``docs/polyfit-mitigation-prereg.md``: with every policy disabled the
mirror must be bit-identical to upstream ``polyfit_offsets`` within
the same environment. The module is skipped when the upstream module
is not importable.
"""

import numpy as np
import pytest

from polyfit_sensitivity import (load_offsets_polyfit,
                                 make_pure_synthetic,
                                 production_fit_kwargs)
from polyfit_mitigation import (CANDIDATES, Policy, batch_size,
                                polyfit_candidate)

try:
    op = load_offsets_polyfit()
except ImportError:
    op = None

pytestmark = pytest.mark.skipif(
    op is None, reason="upstream offsets_polyfit not importable")

KWARGS = production_fit_kwargs()


@pytest.fixture(scope="module")
def noisy_case():
    """A 12x12 synthetic case with two planted outliers."""
    data, _ = make_pure_synthetic(n_az=12, n_rg=12, noise_std=0.02,
                                  seed=3)
    data[20, 3] += 40.0
    data[77, 4] -= 25.0
    return data


def _upstream(data, **overrides):
    kw = dict(KWARGS, **overrides)
    return op.polyfit_offsets(data.copy(),
                              max_iterations=overrides.pop(
                                  "max_iterations", len(data)),
                              **{k: v for k, v in kw.items()
                                 if k != "max_iterations"})


def _assert_bit_identical(upstream, result):
    np.testing.assert_array_equal(upstream["coefL"], result["coefL"])
    np.testing.assert_array_equal(upstream["coefP"], result["coefP"])
    np.testing.assert_array_equal(upstream["inliers"],
                                  result["inliers"])
    assert upstream["removed_indices"] == result["removed_indices"]


# ------------------------------------------------------------------
# Disabled-mode equivalence (the Phase-A gate)

@pytest.mark.parametrize("seed", [1, 2, 3, 29])
def test_disabled_policy_bit_identical(seed):
    data, _ = make_pure_synthetic(seed=seed)
    res, trace = polyfit_candidate(op, data, policy=None, **KWARGS)
    _assert_bit_identical(_upstream(data), res)
    assert res["stop_reason"] == "w_test"
    assert res["converged"]
    assert len(trace) == res["refits"]


def test_disabled_policy_object_equals_none(noisy_case):
    r1, _ = polyfit_candidate(op, noisy_case, policy=None, **KWARGS)
    r2, _ = polyfit_candidate(op, noisy_case, policy=Policy(),
                              **KWARGS)
    _assert_bit_identical(r1, r2)


def test_c6_zero_budget_bit_identical(noisy_case):
    """C6 = Policy() with max_iterations=0 (iteration-0 fit)."""
    upstream = _upstream(noisy_case, max_iterations=0)
    res, trace = polyfit_candidate(op, noisy_case, policy=Policy(),
                                   max_iterations=0, **KWARGS)
    _assert_bit_identical(upstream, res)
    assert res["stop_reason"] in ("w_test", "max_refits")
    assert res["n_removed"] == 0
    assert len(trace) == 1


def test_c4_literal_degenerate_below_2500(noisy_case):
    """Pre-registered: literal C4 == upstream for n < 2500."""
    res, _ = polyfit_candidate(op, noisy_case,
                               policy=CANDIDATES["C4"], **KWARGS)
    _assert_bit_identical(_upstream(noisy_case), res)
    assert all(k == 1 for k in res["batch_sizes"])


def test_neutral_guards_bit_identical(noisy_case):
    """Guard fields at their no-op extremes must not change results.

    The Nunk+1 depletion cap (active with any enabled policy) may not
    bind on this fixture: upstream retains far more than Nunk+1.
    """
    neutral = Policy(min_inlier_frac=0.0, max_reject_frac=1.0)
    res, _ = polyfit_candidate(op, noisy_case, policy=neutral,
                               **KWARGS)
    _assert_bit_identical(_upstream(noisy_case), res)


# ------------------------------------------------------------------
# Frozen k-schedule and presets

def test_batch_size_schedule():
    c4, c4b = CANDIDATES["C4"], CANDIDATES["C4b"]
    assert [batch_size(n, c4) for n in (900, 2499, 2500, 4999)] \
        == [1, 1, 1, 1]
    assert [batch_size(n, c4) for n in (5000, 5001, 12500, 40000)] \
        == [2, 2, 5, 16]
    assert [batch_size(n, c4b) for n in (900, 4999, 5000, 7500)] \
        == [2, 2, 2, 3]
    assert batch_size(40000, Policy()) == 1


def test_presets_match_preregistration():
    assert CANDIDATES["C0"].is_disabled()
    c2b = CANDIDATES["C2b"]
    assert (c2b.min_inlier_frac, c2b.min_inlier_count,
            c2b.max_reject_frac) == (0.10, 60, 0.75)
    assert CANDIDATES["C3"].deadband_q == pytest.approx(1.0 / 32.0)
    assert CANDIDATES["C3"].deadband_eligibility
    assert (CANDIDATES["C4"].batch_divisor,
            CANDIDATES["C4"].batch_min) == (2500.0, 1)
    assert (CANDIDATES["C4b"].batch_divisor,
            CANDIDATES["C4b"].batch_min) == (2500.0, 2)
    combo = CANDIDATES["C4+C3"]
    assert combo.deadband_q == pytest.approx(1.0 / 32.0)
    assert combo.batch_min == 2


# ------------------------------------------------------------------
# Guard stop reasons

def test_min_inlier_guard_stops(noisy_case):
    n0 = len(noisy_case)
    res, _ = polyfit_candidate(op, noisy_case,
                               policy=Policy(min_inlier_frac=0.9),
                               **KWARGS)
    floor = int(np.ceil(0.9 * n0))
    assert res["stop_reason"] == "min_inliers"
    assert not res["converged"]
    assert res["n_inliers"] == floor
    assert res["n_removed"] == n0 - floor


def test_max_rejection_guard_stops(noisy_case):
    n0 = len(noisy_case)
    res, _ = polyfit_candidate(op, noisy_case,
                               policy=Policy(max_reject_frac=0.05),
                               **KWARGS)
    assert res["stop_reason"] == "max_rejections"
    assert res["n_removed"] == int(np.floor(0.05 * n0))


def test_guard_priority_min_inliers_first(noisy_case):
    """When both guards bind at once, min_inliers is reported."""
    n0 = len(noisy_case)
    # Floor and budget chosen to bind on the same removal (count-based
    # floor avoids ceil() edge cases; floor(3.49) = 3 rejections).
    policy = Policy(min_inlier_count=n0 - 3,
                    max_reject_frac=3.49 / n0)
    res, _ = polyfit_candidate(op, noisy_case, policy=policy,
                               **KWARGS)
    assert res["stop_reason"] == "min_inliers"
    assert res["n_removed"] == 3


def test_max_refits_reason(noisy_case):
    res, trace = polyfit_candidate(op, noisy_case, policy=Policy(),
                                   max_iterations=3, **KWARGS)
    _assert_bit_identical(_upstream(noisy_case, max_iterations=3),
                          res)
    assert res["stop_reason"] == "max_refits"
    assert res["n_removed"] == 3


# ------------------------------------------------------------------
# Deadband eligibility (C3 primary rule vs rejected stop-only variant)

def _deadband_fixture():
    """A compliant high-weight point carrying the top removal score.

    24 low-weight junk points have raw residuals far outside the
    deadband while the driver's residual (q/4) is inside it; the
    driver's high weight still gives it the largest standardized
    combined score. The stop-only variant would remove the driver
    first; the eligibility rule must never remove it.
    """
    q = 1.0 / 32.0
    lines = np.linspace(0.0, 100.0, 5)
    pixels = np.linspace(0.0, 100.0, 5)
    ll, pp = (a.ravel() for a in np.meshgrid(lines, pixels,
                                             indexing="ij"))
    n = ll.size
    d_l = np.zeros(n)
    d_p = np.zeros(n)
    w = np.full(n, 0.05)
    junk = np.arange(n) != 12
    signs = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    d_l[junk] = 0.5 * signs[junk]
    driver = 12
    # The weighted fit chases the high-leverage driver (~92% on this
    # design): the input offset 0.1 px leaves a post-fit residual
    # ~0.008 px, under the q/2 = 0.0156 px floor, while the small
    # redundancy s_i of the high-weight point pushes its standardized
    # statistic to the top of the ranking (the #351 high-weight-tail
    # mechanism).
    d_l[driver] = 0.1
    w[driver] = 0.9485
    data = np.column_stack([np.arange(n, dtype=float), ll, pp,
                            d_l, d_p, w])
    kwargs = dict(degree=2, crit_value=0.1, minL=0.0, maxL=100.0,
                  minP=0.0, maxP=100.0, prf=1520.0,
                  abw=1263.68013808518, rsr=4.8e7, rbw=4.0e7)
    return data, driver, q, kwargs


def test_deadband_eligibility_protects_compliant_point():
    data, driver, q, kwargs = _deadband_fixture()
    eligibility = Policy(deadband_q=q)
    res, _ = polyfit_candidate(op, data, policy=eligibility, **kwargs)
    assert driver not in res["removed_indices"]
    # Depending on how far the junk purge proceeds the loop ends via
    # the all-compliant stop or the Nunk+1 depletion floor; either
    # way the compliant driver survived, which is the property under
    # test.
    assert res["stop_reason"] in ("w_test", "min_inliers")

    stop_only = Policy(deadband_q=q, deadband_eligibility=False)
    res2, _ = polyfit_candidate(op, data, policy=stop_only, **kwargs)
    assert res2["removed_indices"][0] == driver


def test_deadband_fixture_driver_tops_upstream_ranking():
    """The fixture is only meaningful if upstream removes the driver
    first — i.e. the compliant point genuinely carries the top score.
    """
    data, driver, _, kwargs = _deadband_fixture()
    upstream = op.polyfit_offsets(data.copy(),
                                  max_iterations=len(data), **kwargs)
    assert upstream["removed_indices"][0] == driver


# ------------------------------------------------------------------
# Failure handling and determinism

class _FailingOp:
    """Delegates to the upstream module; cholesky_solve starts
    raising after a set number of successful calls."""

    def __init__(self, op_module, fail_after_calls):
        self._op = op_module
        self._calls = 0
        self._fail_after = fail_after_calls

    def __getattr__(self, name):
        return getattr(self._op, name)

    def cholesky_solve(self, *args, **kwargs):
        self._calls += 1
        if self._calls > self._fail_after:
            raise np.linalg.LinAlgError("injected failure")
        return self._op.cholesky_solve(*args, **kwargs)


def test_rank_failure_graceful_with_policy(noisy_case):
    # Two solves per refit: fail from refit 3 onward (calls 7+).
    shim = _FailingOp(op, fail_after_calls=6)
    res, _ = polyfit_candidate(shim, noisy_case,
                               policy=Policy(max_reject_frac=0.5),
                               **KWARGS)
    assert res["stop_reason"] == "rank_failure"
    assert not res["converged"]
    assert res["n_removed"] == 3
    assert len(res["coefL"]) == 6


def test_rank_failure_disabled_policy_raises(noisy_case):
    shim = _FailingOp(op, fail_after_calls=6)
    with pytest.raises(np.linalg.LinAlgError):
        polyfit_candidate(shim, noisy_case, policy=Policy(), **KWARGS)


def test_aa_determinism_candidate(noisy_case):
    r1, _ = polyfit_candidate(op, noisy_case,
                              policy=CANDIDATES["C3"], **KWARGS)
    r2, _ = polyfit_candidate(op, noisy_case,
                              policy=CANDIDATES["C3"], **KWARGS)
    _assert_bit_identical(r1, r2)
    assert r1["stop_reason"] == r2["stop_reason"]


# ------------------------------------------------------------------
# Instrumentation contract

def test_instrumentation_keys(noisy_case):
    res, _ = polyfit_candidate(op, noisy_case, policy=Policy(),
                               **KWARGS)
    for key in ("stop_reason", "converged", "n_initial", "n_inliers",
                "n_removed", "retention", "refits", "ridge_fallbacks",
                "normal_matrix_cond", "design_rank",
                "spatial_coverage", "batch_sizes",
                "batch_compliant_removed", "final_batch_overshoot",
                "seconds"):
        assert key in res, key
    assert 0.0 < res["retention"] <= 1.0
    assert res["n_initial"] == len(noisy_case)
    assert res["n_inliers"] + res["n_removed"] == res["n_initial"]
    cov = res["spatial_coverage"]
    assert sum(cov["quadrant_counts"]) == res["n_inliers"]
    assert 0.0 <= cov["bbox_area_frac"] <= 1.0
    assert res["design_rank"] == 6
    assert res["normal_matrix_cond"] >= 1.0
    assert len(res["batch_sizes"]) == res["refits"] - 1
