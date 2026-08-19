# Rubbersheet polyfit mitigation comparison — pre-registered results

Companion to [isce-framework/isce3#351](https://github.com/isce-framework/isce3/issues/351).
Protocol: [`docs/polyfit-mitigation-prereg.md`](../docs/polyfit-mitigation-prereg.md),
frozen at `d234d13` **before any candidate result was computed**; one
amendment (A1, C5 convexity correction, `a94f474`) was made from a
convergence failure on discovery data only, before any comparative
result existed. All confirmatory numbers below come from a **single
one-shot run** of the pre-registered protocol — no seed, threshold,
metric or candidate parameter was changed after seeing results.

Date: 2026-08-19. Environment: pinned dev container (isce3
0.26.0-dev at `2919e1c97`, numpy 1.26.4), threads pinned to 1,
per-seed provenance recorded in every artifact JSON.

## TL;DR

1. **The baseline fragility is structural, not anecdotal.** Under
   production settings, ~2% of *all* single-node single-quantum
   (1/32 px) input perturbations change the fitted surface by more
   than 1e-2 px RMS (the frozen material-jump threshold), and a flip
   at the high-weight driver node is material in **26.5%** of 200
   fresh unfiltered synthetic cases. The rate is stable across all
   four pre-registered generator-variant robustness cells.
2. **Every mechanism-level mitigation sketched in #351 relocates the
   jumps rather than removing them.** The quantization deadband (C3)
   and the min-batch-2 variant (C4b) both kill the *recorded*
   real-data jump (driver-flip response 3.60e-2 → ≤1e-4 px) — and
   leave the ensemble material rates essentially unchanged
   (C4b is even slightly worse than baseline). Only candidates that
   change *what is estimated* (retention guard C2b, convex
   Huber-IRLS C5) eliminate the jump class, at the cost of roughly
   doubling the truth error (junk retention). **No candidate passes
   all pre-registered decision gates**, so per the frozen decision
   tree the upstream proposal is observability-first.
3. **bhawkins's literal batch-removal suggestion is a clean runtime
   win**: 3.6× faster on the recorded 40k case (73.5 → 20.4 s,
   38,324 → 9,118 refits) with a 4e-6 px drift and a stability
   profile identical to baseline — exactly the runtime motivation it
   was proposed for in isce3#173, now measured on production-shaped
   data.

## Protocol in one paragraph

Eight candidates (pre-reg section 3): C0 upstream baseline; C2b
retention/rejection guard; C3 quantization deadband (residual floor
q/2, removal-eligibility rule); C4 literal `max(1, floor(n/2500))`
batch removal (current-n/floor reading frozen); C4b `max(2, ...)`
single disclosed variant; C4+C3; C5 convex Huber-IRLS
(evaluation-only); C6 no-rejection weighted LS (frontier anchor).
Arenas (section 4): 200 fresh unfiltered confirmatory seeds
(1000–1199) of the two-population synthetic generator; four
exploratory robustness cells (50 seeds each); the recorded 40k
production case study. Perturbations (section 5): one hashed
20-flip manifest per case shared by all candidates (10
uniform-over-input + 10 weight-decile-stratified, balanced bands and
signs), plus the always-evaluated driver flip. Metrics and gates
(sections 6–7) are quoted where used below. The mirror harness is
bit-identical to upstream with all policies disabled (tested); the
upstream module is imported, never reimplemented.

## Baseline fragility (C0)

Confirmatory ensemble, 200 seeds, per-band L (azimuth) figures:

| estimand | n flips | material rate | response p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| uniform | 2,000 | 0.017 | 0 (exact) | 0 (exact) | 1.06e-2 | 2.29e-2 |
| stratified | 2,000 | 0.021 | 0 (exact) | 0 (exact) | 1.17e-2 | 2.27e-2 |
| driver | 200 | **0.265** | 3.39e-3 | 1.38e-2 | 2.68e-2 | 3.20e-2 |

The p50/p90 zeros are *exact*: in more than 90% of random flips the
perturbed sample is purged at the same position in the removal
chain, the surviving set is unchanged, and the refit is
bit-identical — while the tail reaches several 1e-2 px. That is the
piecewise-constant-with-rare-boundary-crossings response reported in
#351, now measured as a distribution. Baseline retention: median
3.7% (the ~4% high-weight-elite regime of the recorded production
case reproduces on fresh seeds).

## The frontier: jump damping vs estimator fidelity

Confirmatory ensemble (seed medians; material rates pooled over
flips; azimuth band):

| cand | material U | material S | driver rate | driver p99 | drift p50 | truth p50 | retention p50 |
|---|---|---|---|---|---|---|---|
| C0 | 0.017 | 0.021 | 0.265 | 2.68e-2 | 0 | 1.21e-2 | 0.037 |
| C3 | 0.013 | 0.015 | 0.210 | 2.56e-2 | 4.8e-3 | 1.17e-2 | 0.040 |
| C4 | 0.017 | 0.021 | 0.265 | 2.68e-2 | 0 † | 1.21e-2 | 0.037 |
| C4b | 0.020 | 0.024 | 0.290 | 2.67e-2 | 2.3e-3 | 1.22e-2 | 0.036 |
| C4+C3 | 0.009 | 0.015 | 0.235 | 2.74e-2 | 5.2e-3 | 1.17e-2 | 0.040 |
| C2b | 0.001 | 0.000 | 0.005 | 6.4e-3 | 2.7e-2 | **2.63e-2** | 0.250 |
| C5 | 0.000 | 0.000 | 0.000 | 3.1e-3 | 3.3e-2 | **3.05e-2** | 1.0 (ESS) |
| C6 | 0.000 | 0.000 | 0.000 | 2.1e-3 | 2.8e-1 | **2.88e-1** | 1.0 |

† C4 is bit-identical to C0 on this arena by construction
(`n = 900 < 2500` ⇒ k ≡ 1) — carried as the pre-registered
runtime/degeneracy control.

The frontier is sharp: the mechanism-level candidates (C3, C4b,
C4+C3) keep the estimator — truth error equal to or marginally
better than baseline — and keep most of the jump class; the
estimator-changing candidates (C2b, C5, C6) remove the jump class
and pay 2.2–24× the baseline truth error, because they retain junk
influence the baseline purges. Under this generator, the elite-only
fit is simultaneously the most accurate and the least stable
estimator in the roster.

## Recorded 40k case study

Container, production kwargs, recorded GPU-baseline samples; the
recorded driver flip (row 22961, −1/32 px azimuth):

| cand | stop | retention | refits | seconds | drift RMS L | driver-flip RMS L | material |
|---|---|---|---|---|---|---|---|
| C0 | w_test | 0.0419 | 38,324 | 73.5 | — | **3.599e-2** | yes |
| C3 | w_test | 0.0526 | 37,896 | 107.0 | 2.7e-3 | **9.6e-5** | no |
| C4 | w_test | 0.0419 | 9,118 | **20.4** | 4.0e-6 | **3.599e-2** | yes |
| C4b | w_test | 0.0419 | 7,457 | 22.5 | 4.0e-6 | 1.4e-5 | no |
| C4+C3 | w_test | 0.0527 | 7,242 | 23.1 | 2.6e-3 | 1.7e-4 | no |
| C2b | max_rejections | 0.25 | 30,001 | 96.6 | 1.4e-2 | 6.7e-5 | no |
| C5 | irls_converged | 1.0 | 11 | 0.04 | 1.4e-2 | 1.5e-5 | no |
| C6 | max_refits | 1.0 | 1 | 0.02 | 9.4e-2 | 1.4e-5 | no |

Two observations that must be read together:

- **On this one case, C3 and C4b prevent the recorded CPU-vs-GPU
  product difference entirely** (response at the measurement floor)
  at 2.7e-3 / 4.0e-6 px drift.
- **The ensemble shows this is case-level luck, not class-level
  repair**: the same candidates leave the fresh-seed material rates
  at baseline level (C4b slightly above it). Batch removal moves
  membership boundaries to the kth/(k+1)th cutoff and the deadband
  moves the stop surface; both relocate the discontinuity set rather
  than shrinking it.

The literal-C4 row doubles as the runtime measurement: 3.6× wall
(73.5 → 20.4 s), 4.2× fewer refits, 4e-6 px drift, stability profile
identical to baseline (the endgame is sequential below n = 5,000
under the frozen current-n/floor reading — the recorded driver is
removed with ~3,435 samples left).

## Robustness block (exploratory, 50 seeds per cell)

C0's uniform/stratified material rates hold at 1–2% in all four
generator variants (quantizer phase shifted by q/2; driver at the
far corner; elite fraction halved / doubled). The driver-node rate
tracks the elite size strongly — 0.46 at elite fraction 0.04, 0.06
at 0.16 (fewer elite ⇒ each high-weight node more decisive) — and
C3/C4b relocate rather than remove in every cell (C4b reaches 0.56
driver rate in the elite-low cell). C2b and C5 keep the class
suppressed (≤0.02) in all cells. Termination health is perfect
everywhere: zero errors, zero rank failures, all stop reasons as
designed, in 8,800 confirmatory-plus-robustness fits.

## Pre-registered decision

Gate evaluation (section 7) on the confirmatory ensemble + 40k case:

| cand | g1 tail-flip | g2 drift | g3 truth | g4 termination | g5 runtime | all |
|---|---|---|---|---|---|---|
| C3 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4b | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4+C3 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C2b | ✔ | ✔ | ✘ | ✔ | ✔ | ✘ |
| C5 | ✔ | ✔ | ✘ | ✔ | ✔ | ✘ |

**No behavior-changing candidate passes all gates.** Per the frozen
decision tree: the upstream proposal is **observability-first** —
the common instrumentation of this study (stop-reason taxonomy,
retention/inlier-fraction reporting next to the logged coefficients,
condition/coverage diagnostics) plus the noisy-input retention test
promised in the #351 follow-up — with this comparison attached as
direction-informing data. Two data-supported side notes: literal C4
qualifies on its original runtime motivation independently of the
stability question, and the `crit_value` semantics question
(sections 1/7 of the issue) remains the root cal/val lever — the
recorded 160× response drop between crit 0.1 and 0.2 is adjacent
evidence that recalibration, unlike the mechanism-level candidates,
acts on the class.

## Limitations

- The synthetic generator is a two-population idealization
  calibrated on one production pair; the robustness block varies its
  structure but not its family.
- The 40k case study is one recorded pair (single dataset, single
  environment); its numbers are case evidence, not estimates.
- Material-jump rates are pooled over flips; per-seed counts are in
  the artifacts for cluster-aware intervals (n = 200 seeds ⇒
  worst-case ±7 pp at 95% on a binary rate).
- Provenance note: the artifact `generator_commit` fields span two
  commits (`6350809`, `ee802b2`) because the results aggregator was
  committed in the same worktree while the run executed;
  `git diff 6350809 ee802b2` touches only the aggregator and its
  tests — the generator/runner/mirror code was identical throughout
  (verified; some `worktree_dirty` flags stem from the then-untracked
  aggregator files).

## Reproduction

```
# container, threads pinned; one-shot per the pre-registration
python3 scripts/run_mitigation_ensemble.py ensemble \
    --seeds 1000:1200 --out <dir>/confirmatory --jobs 8
python3 scripts/run_mitigation_ensemble.py ensemble \
    --cell quant_phase|driver_corner|elite_low|elite_high \
    --seeds 1200:1250 --out <dir>/robustness_<cell> --jobs 8
python3 scripts/run_mitigation_ensemble.py real40k \
    --npz <recorded npz> --out <dir>/real40k.json
python3 scripts/aggregate_mitigation_results.py \
    --dir <dir> --out summary.json --md frontier.md
```

The recorded-40k npz is not redistributed (see #351); everything
else is generated from seeds and code in this repository.
