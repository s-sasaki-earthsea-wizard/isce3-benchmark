# Pre-registration: comparative evaluation of rubbersheet-polyfit mitigations

Status: FROZEN at the commit that introduces this file. Any later edit to
a frozen item must be recorded in the "Amendments" section at the bottom
with a date and a reason, and re-frozen before further confirmatory runs.

Context: [isce-framework/isce3#351](https://github.com/isce-framework/isce3/issues/351)
reports that, under production settings (`crit_value=0.1`,
`max_iterations=len(data)`), a single 1/32 px change in one input offset
can discontinuously change the final inlier set of
`isce3.math.offsets_polyfit.polyfit_offsets` and jump the fitted surface
three orders of magnitude above the continuous response. The issue
sketches several mitigation directions. This document freezes, **before
any candidate result is computed**, the candidate definitions, the
evaluation metrics, the perturbation and seed protocols, and the
decision gates that will map results to a proposed upstream scope.

Purpose of freezing: none of the candidates below may be tuned, and none
of the judged metrics, thresholds, or sample plans may be altered, after
results are observed. Exploratory analyses beyond this document are
allowed but must be labelled exploratory in the report.

## 1. Ground rules

- The upstream isce3 source tree is not modified. All candidates are
  implemented as policies inside a mirror of the upstream fit loop
  (`scripts/polyfit_sensitivity.py` / `scripts/polyfit_mitigation.py`);
  the upstream module under test is imported, never reimplemented.
- Mirror fidelity is a test deliverable: with every policy disabled, the
  mirror must be bit-identical to upstream `polyfit_offsets` on the same
  inputs (same pinned environment). Across environments, the required
  equivalence is numerical tolerance plus identical discrete removal
  chains (known ~2-ULP coefficient tails).
- Confirmatory numbers are produced in the pinned dev-container
  environment (isce3 0.26.0-dev at `2919e1c97`, numpy 1.26.4), threads
  pinned to 1. Host runs are development only.
- The discovery datasets (synthetic seed 29, its 7/40 and 22/40 hunt
  populations, seeds 0–39 generally, and the recorded 40k replay) are
  discovery/stress data. They are used for regression tests and
  exploratory sensitivity only, never as the confirmatory sample.
- Every confirmatory artifact records: generator code commit, seed list,
  RNG construction, input-data hashes, flip-manifest hash, environment
  provenance, and thread pinning.

## 2. Common fit settings

All candidates run with the production call shape
(`rubbersheet.py`): `degree=2`, `crit_value=0.1`,
`max_iterations=len(data)`, normalization bounds = full radar grid
(41040, 52906), sensor priors `prf=1520.0`, `abw=1263.68013808518`,
`rsr=4.8e7`, `rbw=4.0e7` (⇒ `sigmaL≈0.124705 px`, `sigmaP=0.083 px`).
The offset quantum is `q = 1/32 px` (Ampcor correlation-surface grid).
`q` is a parameter of the harness, recorded per run; it is never
hard-coded into a candidate rule.

## 3. Candidates (frozen definitions)

Notation: at each iteration the mirror computes, exactly as upstream,
weighted normal-equation fits per band, residuals `eL, eP`, redundancy
numbers `s_i = sqrt(diag_Qe)_i`, and standardized statistics
`wL_i = eL_i/(s_i·sigmaL)`, `wP_i = eP_i/(s_i·sigmaP)`.
`n0` = initial sample count, `n` = current sample count,
`Nunk = ncoeffs(degree) = 6`.

- **C0 — upstream baseline.** All policies disabled; bit-identical to
  `polyfit_offsets` (gated by test).
- **C2b — termination guard** (behavior-changing part of the issue's
  direction 2; the observability part, C2a, is common instrumentation
  and not a candidate). Stop the loop with the current fit when the next
  removal would leave fewer than
  `max(ceil(0.10 · n0), 60)` samples (10% retention floor, absolute
  floor 60 = 10·Nunk), or when total removals would exceed
  `floor(0.75 · n0)` (max-rejection budget). Guard priority when both
  bind in the same iteration: `min_inliers`, then `max_rejections`.
  C2b is evaluated as a termination policy and is **excluded from the
  estimator ranking** (its unperturbed delta is ~0 by construction).
- **C3 — quantization deadband.** Point `i` is *compliant* in band L iff
  `|eL_i| <= max(crit_value · s_i · sigmaL, q/2)` (band P analogous:
  floor `q/2`, scale `sigmaP`). The loop stops when every point is
  compliant in both bands. Removal eligibility: only non-compliant
  points enter the removal ranking (**eligibility rule — primary**).
  The stop-only variant (deadband in the stop test, removal still ranks
  all points) is REJECTED as the primary spec because stopping is
  componentwise while removal ranks `wL²+wP²`: a compliant point could
  still be removed while another point keeps the loop running. It is
  retained only as a unit-test case demonstrating the inconsistency.
  The `q/2` floor is an explicit policy hypothesis (half-quantum error
  bound), not a statistically derived residual floor.
- **C4 — batch removal, bhawkins-literal**
  ([isce3#173 review comment](https://github.com/isce-framework/isce3/pull/173#issuecomment-3563677125)):
  remove the `k = max(1, floor(n / 2500))` worst points per iteration,
  ranked by `wL²+wP²` among eligible points. Frozen interpretation of
  the under-specified formula: `n` = **current** sample count, integer
  part via **floor**. Consequence: `k = 1` for `n < 5000`, so the
  literal rule is **identical to C0 for the synthetic-900 arena** and
  its endgame on the 40k arena (the recorded driver is removed with
  ≈3,435 samples left) is sequential. C4 is therefore carried as the
  historical/runtime control; it is expected to be uninformative about
  the membership jump on synthetic-900 by construction. The
  alternative initial-`n` reading (constant `k = 16` on the 40k case)
  is noted and not run.
  Batch mechanics (shared by C4/C4b): selection = the `k` largest
  combined scores among eligible points, ordered descending, ties
  broken by current-array order (matching upstream `argmax` first-hit
  semantics at `k=1`); `k` is capped so that the loop never removes
  past an active guard floor nor below `Nunk+1` samples; the refit
  budget (`max_refits`, upstream `max_iterations` semantics) is
  separate from the rejection budget (total removed points). Per batch,
  the number of stop-compliant rows removed is recorded; the value for
  the final executed batch is reported as the batch overshoot.
- **C4b — minimum batch of two**: `k = max(2, floor(n / 2500))`. The
  **single** pre-registered diagnostic variant of C4 (disclosed as a
  variant, not as bhawkins's rule). No other floor value will be run;
  no floor tuning after results.
- **C4+C3**: batch mechanics over the C3-eligible (non-compliant) set
  with the C3 stop rule, `k` per C4b (`max(2, floor(n/2500))`) so the
  combination is non-degenerate on synthetic-900.
- **C5 — convex robust loss (evaluation-only reference).** Huber-IRLS:
  joint standardized residual `r_i = sqrt(wL_i² + wP_i²)` (one weight
  vector serves both bands, matching the upstream structure); Huber
  factor `u_i = min(1, c / r_i)` with tuning constant `c = 1.345`;
  fixed scale = the a-priori sigmas (no scale re-estimation); total
  weight per iteration = `corr_peak_i · u_i` clipped to upstream's
  `[eps, 1]`; iterate to convergence: max abs coefficient change
  `< 1e-12` (both bands) or 200 iterations. Reported "retention"
  substitute: Kish effective sample size `(Σu)²/Σu²` and the downweight
  distribution. Implementation timebox: one working day; if exceeded,
  C5 is dropped and the drop is recorded. Not a PR candidate.
- **C6 — no-rejection weighted LS.** The iteration-0 weighted fit
  (upstream first iterate) reported as a candidate row. Anchors the
  junk-retention extreme of the frontier.
- **C1 — crit_value recalibration** is treated as data only: the
  existing sweep on C0 is extended to
  `crit ∈ {0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 1.96, 3.29}`. Recalibration
  is a NISAR cal/val decision and is not proposed as a code change.

## 4. Arenas

- **Synthetic-900 (confirmatory)**: the pinned two-population generator
  (`make_min_repro_case` family) at 30×30 nodes on the production grid,
  driver node and weight as published in #351. Confirmatory seeds:
  **1000–1199 inclusive (200 seeds), unfiltered** — every seed is run
  and reported, including no-response and degenerate cases; failures
  are retained as data. Rationale for 200: for a binary material-jump
  rate, worst-case 95% CI half-width ≈ ±7 percentage points.
- **Recorded-40k replay (case study)**: the recorded GPU-baseline
  sample set with the recorded −1/32 px driver flip, production kwargs,
  threads pinned. One base fit + one flip fit + runtime per candidate.
  Explicitly a case study, not an inferential sample.
- **Robustness block (exploratory, pre-registered)**: three generator
  dimensions, two settings each, 50 fresh seeds each (1200–1249 per
  cell): (a) quantization phase: quantizer grid shifted by `q/2`;
  (b) driver location/leverage: driver at the nearest-corner node
  instead of the published position; (c) weight structure: elite
  fraction 0.04 and 0.16 (published: 0.08). Labelled exploratory.

## 5. Perturbation protocol (flip manifest)

One manifest per case (seed), generated from the case seed by a
dedicated RNG stream, hashed (sha256), and **shared by all candidates**
(common random numbers). Per case, 20 single-node flips:

- Estimand A, uniform-over-input: 10 nodes drawn uniformly from all 900
  sample nodes.
- Estimand B, weight-stratified: 10 nodes, one drawn per weight decile
  (deciles computed from the case's input weights only).
- Within each estimand: 5 flips in band L, 5 in band P; signs balanced
  (+q / −q alternating within band groups). Node/band/sign assignment
  comes only from inputs and the manifest RNG — never from any
  candidate or upstream outcome.
- The published driver node with the −1/32 px azimuth flip is always
  evaluated additionally, reported separately (it is a discovery-based
  stress case, not part of estimands A/B).

Per candidate and case: 1 base fit + 20 manifest flips (+1 driver flip).
Estimands A and B are analyzed separately; seed is the resampling
cluster for any interval statement.

## 6. Metrics (frozen definitions)

Surfaces are evaluated on a fixed 101×101 uniform grid over the full
radar grid, per band, via `predict_offsets`.

Judged (confirmatory):

- **Flip response** (benefit axis): per flip, RMS over the grid of
  (candidate flipped-fit surface − same candidate's base-fit surface),
  per band. Reported as distributions (p50 / p90 / p99 / max) per
  estimand; **ranking uses p99 and the material-jump exceedance rate**.
- **Material jump**: a flip whose response RMS exceeds **1e-2 px** in
  either band (≈3× below the recorded production jump 3.6e-2 px, ≈10³×
  above the continuous-response scale ~1e-5 px). Exceedance rate =
  fraction of manifest flips that are material, per estimand.
- **Unperturbed drift** (cost axis): RMS over the grid of (candidate
  base-fit surface − C0 base-fit surface) per band, distribution over
  seeds; plus the same quantity on the 40k case study.
- **Truth error**: RMS over the grid of (candidate base-fit surface −
  true synthetic surface) per band. Guards against stability-by-
  retaining-junk; C6 exists to make this axis visible.
- **Termination health**: stop-reason distribution, failure rate
  (rank/factorization, budget exhaustion), refit count, and (batch
  candidates) final-batch overshoot.
- **Runtime**: thread-pinned wall time per fit (synthetic and 40k).

Descriptive (always reported, not ranked on): retention or Kish ESS,
membership Jaccard vs C0 final inliers (where membership is defined),
final design condition number, spatial coverage of final inliers
(quadrant counts + normalized bbox area), sample maxima.

## 7. Decision gates (map from results to proposed upstream scope)

A behavior-changing mitigation is proposed as PR scope only if, on the
confirmatory ensemble and the 40k case study, ALL of:

1. Material-jump exceedance rate ≤ 0.1× C0's rate (≥10× reduction), in
   both estimands, and driver-flip response < 1e-2 px on the 40k case.
2. Unperturbed drift: seed-median RMS ≤ 3.6e-2 px per band on
   synthetic, and ≤ 3.6e-2 px on the 40k case (anchor: the recorded
   CPU-vs-GPU backend divergence this class of defect produced — a
   mitigation may not shift the baseline more than the discrepancy it
   removes).
3. Truth error: seed-median ≤ 1.05× C0's seed-median per band.
4. Termination health: zero rank/failure terminations; no case ends by
   `max_refits`.
5. Runtime: ≤ 1.5× C0 on the 40k case study.

If no behavior-changing candidate passes all gates: the upstream
proposal is observability-first (C2a instrumentation + stop-reason
reporting + the noisy-input retention test promised in #351's follow-up
comment), with the comparative data attached to inform direction.
If only C1 (crit recalibration) helps: hand back to cal/val with data.
If only C5 helps: report/RFC scope, not a PR.
C2b may ride along as an opt-in guard only if its gate-2/3 numbers are
clean; it is never ranked as an estimator.

Reporting stance (#351 comment): the results comment opens by stating
that no direction was agreed, that these are benchmark-only mirror
implementations (isce3 source untouched, not a fork), and that the
direction remains open; @bhawkins is mentioned only next to the
literal-C4 result.

## 8. Amendments

(none)
