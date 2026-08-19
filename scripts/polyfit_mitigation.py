"""Policy-aware mirror of the upstream rubbersheet polyfit loop.

Implements the pre-registered mitigation candidates of
``docs/polyfit-mitigation-prereg.md`` as composable policies on top of
the traced-mirror discipline of ``polyfit_sensitivity.py``. The
upstream ``isce3.math.offsets_polyfit`` module under test is imported
(never reimplemented); with every policy field disabled the loop is
bit-identical to upstream ``polyfit_offsets`` on the same inputs (see
``tests/test_polyfit_mitigation.py``).

Policy semantics (frozen in the pre-registration document):

* ``min_inlier_frac`` / ``min_inlier_count`` — stop with the current
  fit rather than remove past the retention floor (stop reason
  ``min_inliers``).
* ``max_reject_frac`` — total-rejection budget as a fraction of the
  initial sample count (stop reason ``max_rejections``).
* ``deadband_q`` — quantization deadband: point ``i`` is *compliant*
  in a band iff ``|e_i| <= max(crit_value * s_i * sigma, q/2)``; the
  loop stops when every point is compliant in both bands. With
  ``deadband_eligibility`` (the pre-registered primary rule) only
  non-compliant points enter the removal ranking; the stop-only
  variant (``deadband_eligibility=False``) is retained solely as the
  unit-tested demonstration of the componentwise-stop /
  ranked-removal inconsistency.
* ``batch_divisor`` / ``batch_min`` — remove
  ``k = max(batch_min, floor(n_current / batch_divisor))`` worst
  points per refit, ranked by ``wL**2 + wP**2`` among eligible
  points, ordered descending, ties broken by current-array order
  (identical to upstream ``argmax`` first-hit semantics at ``k=1``).

The refit budget (``max_iterations``, upstream semantics) is separate
from the rejection budget (``max_reject_frac``); a batch is capped so
the loop never removes past an active guard floor nor below
``Nunk + 1`` samples. Guard priority when several bind in the same
refit: ``min_inliers``, then ``max_rejections``.

The C6 reference (no-rejection weighted LS) is ``Policy()`` with
``max_iterations=0``: the loop returns the iteration-0 fit, which is
bit-identical to upstream under the same budget.

The C5 reference (convex robust loss, evaluation-only) is
``huber_irls_fit``: no membership decisions at all — every sample is
kept and continuously downweighted by a Huber factor on the joint
standardized residual, with the scale fixed at the a-priori sigmas
(frozen spec, pre-registration section 3).
"""

import dataclasses
import math
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from polyfit_sensitivity import (PROD_CRIT_VALUE, PROD_DEGREE,  # noqa: E402
                                 OFFSET_QUANTUM, log)


@dataclasses.dataclass(frozen=True)
class Policy:
    """One mitigation candidate as a set of loop policies.

    All fields disabled (the default) reproduces upstream exactly.

    Attributes:
        min_inlier_frac: Retention floor as a fraction of the initial
            sample count (C2b), or None.
        min_inlier_count: Absolute retention floor in samples (C2b),
            or None. When both floors are set the larger one binds.
        max_reject_frac: Total-rejection budget as a fraction of the
            initial sample count (C2b), or None.
        deadband_q: Offset quantum q [px] for the quantization
            deadband (C3), or None. The residual floor is q/2.
        deadband_eligibility: When True (pre-registered primary rule)
            deadband-compliant points are exempt from the removal
            ranking, not only from the stop test.
        batch_divisor: Batch-removal divisor (C4/C4b: 2500.0), or
            None for one-at-a-time removal.
        batch_min: Minimum batch size (C4: 1, C4b: 2). Only
            meaningful when batch_divisor is set.
    """

    min_inlier_frac: float | None = None
    min_inlier_count: int | None = None
    max_reject_frac: float | None = None
    deadband_q: float | None = None
    deadband_eligibility: bool = True
    batch_divisor: float | None = None
    batch_min: int = 1

    def is_disabled(self):
        """True when every behavior-changing field is off (C0)."""
        return (self.min_inlier_frac is None
                and self.min_inlier_count is None
                and self.max_reject_frac is None
                and self.deadband_q is None
                and self.batch_divisor is None)


# Pre-registered candidate presets (docs/polyfit-mitigation-prereg.md
# section 3). C6 is Policy() run with max_iterations=0; C5 (Huber-IRLS)
# is a separate fitter, not a removal-loop policy.
CANDIDATES = {
    "C0": Policy(),
    "C2b": Policy(min_inlier_frac=0.10, min_inlier_count=60,
                  max_reject_frac=0.75),
    "C3": Policy(deadband_q=OFFSET_QUANTUM),
    "C4": Policy(batch_divisor=2500.0, batch_min=1),
    "C4b": Policy(batch_divisor=2500.0, batch_min=2),
    "C4+C3": Policy(deadband_q=OFFSET_QUANTUM,
                    batch_divisor=2500.0, batch_min=2),
}


# Dedicated RNG substream tag for flip manifests (pre-registration
# section 5): rng = default_rng([case_seed, MANIFEST_STREAM]).
MANIFEST_STREAM = 351


def build_flip_manifest(case_seed, weights, q=OFFSET_QUANTUM,
                        n_uniform=10, n_stratified=10):
    """Build the frozen per-case flip manifest (pre-reg section 5).

    One manifest per case, generated from the case seed by a
    dedicated RNG stream and shared by all candidates (common random
    numbers). Two estimands: ``uniform`` (nodes drawn uniformly over
    all samples) and ``stratified`` (one node per weight decile,
    deciles computed from the input weights only). Within each
    estimand the first half of the draws flips band L and the second
    half band P, signs alternating +q/-q within each band group —
    the assignment uses only the inputs and the manifest RNG, never
    a candidate or upstream outcome.

    Args:
        case_seed: The case (generator) seed.
        weights: (N,) input correlation-peak weights of the case.
        q: Offset quantum [px].
        n_uniform: Flips in the uniform estimand.
        n_stratified: Flips in the stratified estimand (= number of
            weight strata).

    Returns:
        dict: ``{"case_seed", "q", "flips", "sha256"}`` where flips
        is a list of ``{"estimand", "node", "band", "sign",
        "delta"}`` and the hash covers the canonical JSON of
        everything but itself.
    """
    import hashlib
    import json

    weights = np.asarray(weights, dtype=float)
    n = weights.size
    if n < max(n_uniform, n_stratified):
        raise ValueError("case smaller than the manifest draw")
    rng = np.random.default_rng([int(case_seed), MANIFEST_STREAM])

    uniform_nodes = [int(i) for i in
                     rng.choice(n, size=n_uniform, replace=False)]
    order = np.argsort(weights, kind="stable")
    strata = np.array_split(order, n_stratified)
    stratified_nodes = [int(rng.choice(group)) for group in strata]

    flips = []
    for estimand, nodes in (("uniform", uniform_nodes),
                            ("stratified", stratified_nodes)):
        half = len(nodes) // 2
        for j, node in enumerate(nodes):
            band = "L" if j < half else "P"
            sign = 1 if j % 2 == 0 else -1
            flips.append({"estimand": estimand, "node": node,
                          "band": band, "sign": sign,
                          "delta": sign * q})
    body = {"case_seed": int(case_seed), "q": q, "flips": flips}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    return {**body, "sha256": digest}


def apply_flip(data, flip):
    """Return a copy of the case with one manifest flip applied."""
    flipped = np.asarray(data, dtype=float).copy()
    column = 3 if flip["band"] == "L" else 4
    flipped[flip["node"], column] += flip["delta"]
    return flipped


def batch_size(n_current, policy):
    """Scheduled batch size before caps (frozen k-schedule).

    Args:
        n_current: Current sample count.
        policy: The Policy in effect.

    Returns:
        int: ``max(batch_min, floor(n_current / batch_divisor))``, or
        1 when batch removal is disabled.
    """
    if policy.batch_divisor is None:
        return 1
    return max(policy.batch_min,
               math.floor(n_current / policy.batch_divisor))


def _spatial_coverage(data, minL, maxL, minP, maxP):
    """Quadrant counts and normalized bbox area of the final inliers."""
    lines, pixels = data[:, 1], data[:, 2]
    midL, midP = 0.5 * (minL + maxL), 0.5 * (minP + maxP)
    north, west = lines < midL, pixels < midP
    quadrants = [int((north & west).sum()), int((north & ~west).sum()),
                 int((~north & west).sum()), int((~north & ~west).sum())]
    spanL = (lines.max() - lines.min()) / (maxL - minL) if len(lines) else 0.0
    spanP = (pixels.max() - pixels.min()) / (maxP - minP) if len(pixels) else 0.0
    return {"quadrant_counts": quadrants,
            "bbox_area_frac": float(spanL * spanP)}


def polyfit_candidate(op, data, policy=None, degree=PROD_DEGREE,
                      crit_value=PROD_CRIT_VALUE, max_iterations=None,
                      minL=None, maxL=None, minP=None, maxP=None,
                      prf=None, abw=None, rsr=None, rbw=None,
                      coef_log=None, progress_label=None):
    """Run the mirrored fit loop under a mitigation policy.

    Replicates the upstream operation order exactly (fit, residuals,
    w-test, removal); the policy only alters the stop test, the
    removal eligibility, and the number of points removed per refit.
    With ``policy=None`` or ``Policy()`` the result is bit-identical
    to upstream ``polyfit_offsets``.

    Args:
        op: The upstream offsets_polyfit module (basis and solver
            functions are taken from it, not reimplemented).
        data: (N, 6) input array (copied internally).
        policy: A Policy instance (default: all-off = C0).
        degree, crit_value, max_iterations, minL, maxL, minP, maxP,
        prf, abw, rsr, rbw: As in upstream ``polyfit_offsets``;
            ``max_iterations=None`` means ``len(data)`` (the
            production call shape). ``max_iterations`` is the refit
            budget; the rejection budget lives in the policy.
        coef_log: Optional list collecting per-refit ``(coefL, coefP)``
            copies, including the final one.
        progress_label: Optional label for stderr progress lines.

    Returns:
        tuple: (result dict, trace list). The result is
        upstream-shaped (``coefL``, ``coefP``, ``inliers``,
        ``removed_indices``, ``degree``, ``design_nunk``) plus the
        instrumentation keys ``stop_reason`` (``w_test`` |
        ``min_inliers`` | ``max_rejections`` | ``max_refits`` |
        ``rank_failure``), ``converged``, ``n_initial``,
        ``n_inliers``, ``n_removed``, ``retention``, ``refits``,
        ``ridge_fallbacks``, ``normal_matrix_cond``, ``design_rank``,
        ``spatial_coverage``, ``batch_sizes``,
        ``batch_compliant_removed``, ``final_batch_overshoot`` and
        ``seconds``.

    Raises:
        ValueError: When ``Nobs <= Nunk`` at a refit (upstream
            behavior; unreachable once a retention guard is active).
        numpy.linalg.LinAlgError: When the ridge-stabilized solve
            fails with a disabled policy (upstream behavior) or on
            the very first refit. With an enabled policy and at least
            one successful refit the loop returns the last successful
            coefficients with ``stop_reason='rank_failure'`` and the
            current (post-removal) sample set instead.
    """
    p = policy if policy is not None else Policy()
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
    n0 = data.shape[0]

    floors = []
    if p.min_inlier_frac is not None:
        floors.append(math.ceil(p.min_inlier_frac * n0))
    if p.min_inlier_count is not None:
        floors.append(p.min_inlier_count)
    min_keep = max(floors) if floors else None
    reject_cap = (math.floor(p.max_reject_frac * n0)
                  if p.max_reject_frac is not None else None)
    guard_active = not p.is_disabled()

    removed_indices = []
    batch_sizes, batch_compliant = [], []
    trace = []
    ridge_fallbacks = 0
    last_coefs = None
    t0 = time.perf_counter()

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
            ridge_fallbacks += 1
            I = np.eye(Nmat.shape[0])
            try:
                xL, Lc = op.cholesky_solve(Nmat + eps * I, rhsL)
                xP, _ = op.cholesky_solve(Nmat + eps * I, rhsP)
            except np.linalg.LinAlgError:
                if p.is_disabled() or last_coefs is None:
                    raise
                # Last successful coefficients, current sample set.
                xL_prev, xP_prev = last_coefs
                return (_result(xL_prev, xP_prev, data, removed_indices,
                                degree, nunk, "rank_failure", n0,
                                iteration, ridge_fallbacks, float("nan"),
                                0, minL, maxL, minP, maxP, batch_sizes,
                                batch_compliant, t0), trace)
        Qx_hat = op.invert_from_cholesky(Lc)

        eL, eP = yL - A @ xL, yP - A @ xP
        Qyhat_diag = np.einsum("ij,jk,ik->i", A, Qx_hat, A)
        diag_Qe = np.clip(Qy_diag - Qyhat_diag, eps, None)
        s = np.sqrt(diag_Qe)
        wL, wP = eL[:, 0] / (s * sigmaL), eP[:, 0] / (s * sigmaP)
        max_any = max(np.abs(wL).max(), np.abs(wP).max())
        last_coefs = (xL, xP)
        if coef_log is not None:
            coef_log.append((xL[:, 0].copy(), xP[:, 0].copy()))
        if progress_label is not None and iteration % 5000 == 0:
            log(f"{progress_label}: iteration {iteration}, "
                f"n_obs {nobs}")

        # Stop test: upstream expression verbatim when the deadband is
        # off; the all-compliant raw-residual form when it is on.
        if p.deadband_q is None:
            stop_w = max_any <= crit_value
            compliant = None
            eligible = None
        else:
            half_q = 0.5 * p.deadband_q
            tolL = np.maximum(crit_value * s * sigmaL, half_q)
            tolP = np.maximum(crit_value * s * sigmaP, half_q)
            compliant = ((np.abs(eL[:, 0]) <= tolL)
                         & (np.abs(eP[:, 0]) <= tolP))
            stop_w = bool(compliant.all())
            eligible = ~compliant if p.deadband_eligibility else None

        combined = wL * wL + wP * wP
        record = {
            "iteration": iteration,
            "n_obs": int(nobs),
            "stop_margin": float(max_any - crit_value),
            "n_eligible": int(eligible.sum()) if eligible is not None
                          else int(nobs),
        }
        trace.append(record)

        stop_reason = None
        if stop_w:
            stop_reason = "w_test"
        elif iteration >= max_iterations:
            stop_reason = "max_refits"
        else:
            k = batch_size(nobs, p)
            if eligible is not None:
                k = min(k, int(eligible.sum()))
            if guard_active:
                k = min(k, nobs - (nunk + 1))
            if min_keep is not None:
                k = min(k, nobs - min_keep)
                if k <= 0:
                    stop_reason = "min_inliers"
            if stop_reason is None and reject_cap is not None:
                k = min(k, reject_cap - len(removed_indices))
                if k <= 0:
                    stop_reason = "max_rejections"
            if stop_reason is None and k <= 0:
                # Only reachable with a policy on (Nunk floor or an
                # empty eligible set): report as the retention floor.
                stop_reason = "min_inliers"

        if stop_reason is not None:
            cond = float(np.linalg.cond(Nmat))
            rank = int(np.linalg.matrix_rank(Nmat))
            return (_result(xL, xP, data, removed_indices, degree,
                            nunk, stop_reason, n0, iteration + 1,
                            ridge_fallbacks, cond, rank, minL, maxL,
                            minP, maxP, batch_sizes, batch_compliant,
                            t0), trace)

        # Removal: k worst by combined score among eligible points,
        # descending, ties by current-array order (== upstream argmax
        # first-hit at k=1).
        if eligible is None:
            scores = combined
        else:
            scores = np.where(eligible, combined, -np.inf)
        if k == 1:
            worst_idx = np.array([int(np.argmax(scores))])
        else:
            worst_idx = np.argsort(-scores, kind="stable")[:k]
        if compliant is not None:
            comp_mask = compliant
        else:
            comp_mask = ((np.abs(wL) <= crit_value)
                         & (np.abs(wP) <= crit_value))
        ids = [int(i) for i in data[worst_idx, 0]]
        record["removed_ids"] = ids
        if k == 1:
            record["removed_id"] = ids[0]
        batch_sizes.append(int(k))
        batch_compliant.append(int(comp_mask[worst_idx].sum()))
        removed_indices.extend(ids)
        data = np.delete(data, worst_idx, axis=0)
        A = np.delete(A, worst_idx, axis=0)


def _result(xL, xP, data, removed_indices, degree, nunk, stop_reason,
            n0, refits, ridge_fallbacks, cond, rank, minL, maxL, minP,
            maxP, batch_sizes, batch_compliant, t0):
    """Assemble the upstream-shaped result dict plus instrumentation."""
    return {
        "coefL": xL[:, 0], "coefP": xP[:, 0],
        "inliers": data,
        "removed_indices": removed_indices,
        "degree": degree, "design_nunk": nunk,
        "stop_reason": stop_reason,
        "converged": stop_reason == "w_test",
        "n_initial": int(n0),
        "n_inliers": int(data.shape[0]),
        "n_removed": len(removed_indices),
        "retention": data.shape[0] / n0,
        "refits": int(refits),
        "ridge_fallbacks": int(ridge_fallbacks),
        "normal_matrix_cond": cond,
        "design_rank": rank,
        "spatial_coverage": _spatial_coverage(data, minL, maxL,
                                              minP, maxP),
        "batch_sizes": batch_sizes,
        "batch_compliant_removed": batch_compliant,
        "final_batch_overshoot": batch_compliant[-1] if batch_compliant
                                 else 0,
        "seconds": time.perf_counter() - t0,
    }


def huber_irls_fit(op, data, c=1.345, tol=1e-12, max_iterations=200,
                   degree=PROD_DEGREE, minL=None, maxL=None,
                   minP=None, maxP=None, prf=None, abw=None,
                   rsr=None, rbw=None):
    """C5 reference: convex Huber-IRLS fit with no membership decisions.

    Every sample is kept; outlyingness is handled by continuous
    downweighting instead of hard deletion. Frozen spec
    (pre-registration section 3, as amended A1): the standardization
    ``s0_i`` is computed ONCE from the base fit (weights =
    ``corr_peak``, the upstream iteration-0 redundancy numbers) and
    held fixed; per refit the joint standardized residual is
    ``r_i = sqrt((eL_i/(s0_i*sigmaL))**2 + (eP_i/(s0_i*sigmaP))**2)``
    with the a-priori sigmas (fixed scale, no re-estimation); the
    Huber factor is ``u_i = min(1, c / r_i)`` and the LS weight is
    ``clip(corr_peak_i * sqrt(u_i), eps, 1)`` (the upstream clip
    range) so the squared-weight normal equations apply the robust
    weight ``u`` — the textbook convex Huber IRLS. Iterate until the
    maximum absolute coefficient change of both bands is below
    ``tol`` or ``max_iterations`` refits.

    (The originally frozen wording — weight ``corr_peak * u`` and
    per-refit re-standardization — is a redescending, non-convex
    estimator under squared weights and does not converge on the
    two-population case; see amendment A1 in the pre-registration.)

    Args:
        op: The upstream offsets_polyfit module.
        data: (N, 6) input array (copied internally).
        c: Huber tuning constant on the joint standardized residual.
        tol: Convergence threshold on the max abs coefficient change.
        max_iterations: Refit budget.
        degree, minL, maxL, minP, maxP, prf, abw, rsr, rbw: As in
            upstream ``polyfit_offsets``.

    Returns:
        dict: Upstream-shaped result (``inliers`` = all samples,
        ``removed_indices`` = []) plus ``stop_reason``
        (``irls_converged`` | ``irls_max_iterations``), ``converged``,
        ``refits``, ``coef_delta`` (final iteration's max abs
        coefficient change), ``ess_kish`` (effective sample size
        ``(sum u)**2 / sum(u**2)`` over the Huber factors),
        ``downweight_quantiles`` (p10/p50/p90 and min of ``u``),
        ``n_downweighted`` (samples with ``u < 1``),
        ``normal_matrix_cond``, ``design_rank``,
        ``spatial_coverage`` and ``seconds``.

    Raises:
        ValueError: When ``Nobs <= Nunk`` (upstream behavior).
    """
    data = np.asarray(data, dtype=float).copy()

    sigmaL = 0.15 / ((prf / abw) if (None not in [prf, abw]) else 1.1)
    sigmaP = 0.10 / ((rsr / rbw) if (None not in [rsr, rbw]) else 1.1)
    maxL = data[:, 1].max() if maxL is None else maxL
    minL = data[:, 1].min() if minL is None else minL
    maxP = data[:, 2].max() if maxP is None else maxP
    minP = data[:, 2].min() if minP is None else minP

    nunk = op.ncoeffs(degree)
    eps = np.sqrt(np.finfo(float).eps)
    nobs = data.shape[0]
    if nobs <= nunk:
        raise ValueError(
            "No sufficient points for the rubbersheet polyfitting")

    A = op.build_design_matrix(data[:, 1], data[:, 2], degree,
                               minL, maxL, minP, maxP)
    yL, yP = data[:, 3:4], data[:, 4:5]
    peak = data[:, 5]
    u = np.ones(nobs)
    u_fit = u
    s0 = None
    prev = None
    coef_delta = float("nan")
    t0 = time.perf_counter()

    for iteration in range(max_iterations):
        w = np.clip(peak * np.sqrt(u), eps, 1.0)
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

        eL, eP = yL - A @ xL, yP - A @ xP
        if s0 is None:
            # Fixed standardization from the base fit (u = 1, weights
            # = corr_peak): the upstream iteration-0 redundancy.
            Qx_hat = op.invert_from_cholesky(Lc)
            Qy_diag = 1.0 / (w * w)
            Qyhat_diag = np.einsum("ij,jk,ik->i", A, Qx_hat, A)
            s0 = np.sqrt(np.clip(Qy_diag - Qyhat_diag, eps, None))

        u_fit = u  # the Huber factors that produced this fit
        if prev is not None:
            coef_delta = max(np.abs(xL - prev[0]).max(),
                             np.abs(xP - prev[1]).max())
            if coef_delta < tol:
                stop_reason = "irls_converged"
                break
        prev = (xL, xP)

        wL = eL[:, 0] / (s0 * sigmaL)
        wP = eP[:, 0] / (s0 * sigmaP)
        r = np.sqrt(wL * wL + wP * wP)
        u = np.ones(nobs)
        outlying = r > c
        u[outlying] = c / r[outlying]
    else:
        stop_reason = "irls_max_iterations"

    q10, q50, q90 = np.quantile(u_fit, [0.1, 0.5, 0.9])
    return {
        "coefL": xL[:, 0], "coefP": xP[:, 0],
        "inliers": data,
        "removed_indices": [],
        "degree": degree, "design_nunk": nunk,
        "stop_reason": stop_reason,
        "converged": stop_reason == "irls_converged",
        "n_initial": int(nobs),
        "n_inliers": int(nobs),
        "n_removed": 0,
        "retention": 1.0,
        "refits": iteration + 1,
        "coef_delta": float(coef_delta),
        "ess_kish": float(u.sum() ** 2 / (u * u).sum()),
        "downweight_quantiles": {"p10": float(q10), "p50": float(q50),
                                 "p90": float(q90),
                                 "min": float(u.min())},
        "n_downweighted": int((u < 1.0).sum()),
        "normal_matrix_cond": float(np.linalg.cond(Nmat)),
        "design_rank": int(np.linalg.matrix_rank(Nmat)),
        "spatial_coverage": _spatial_coverage(data, minL, maxL,
                                              minP, maxP),
        "seconds": time.perf_counter() - t0,
    }
