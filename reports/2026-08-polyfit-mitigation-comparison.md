# Rubbersheet polyfit mitigation comparison — pre-registered results

Companion to [isce-framework/isce3#351](https://github.com/isce-framework/isce3/issues/351).
Protocol: [`docs/polyfit-mitigation-prereg.md`](../docs/polyfit-mitigation-prereg.md),
frozen at `d234d13` **before any confirmatory or comparative result
was computed**; one
amendment (A1, C5 convexity correction, `a94f474`) was made from a
convergence failure on discovery data only, before any comparative
result existed. All confirmatory numbers below come from a **single
one-shot run** of the pre-registered protocol — no seed, threshold,
metric or candidate parameter was changed after seeing results.
(The aggregation code was extended once after team review to add
pre-registered diagnostics that the first aggregation omitted; the
frozen raw archive was re-aggregated, no fits were re-run, and no
gate outcome changed.)

Date: 2026-08-19. Environment: pinned dev container (isce3
0.26.0-dev at `2919e1c97`, numpy 1.26.4), threads pinned to 1,
per-seed provenance recorded in every artifact JSON.

## TL;DR

1. **The baseline sensitivity is structural, not anecdotal.** Under
   production settings, 1.7% of sampled uniform and 2.15% of sampled
   weight-stratified single-node single-quantum (1/32 px)
   perturbations change the fitted surface by more than 1e-2 px RMS
   (the frozen material-jump threshold; each estimand = 200 seeds ×
   10 flips), and a flip at the high-weight driver node is material
   in **26.5%** (53/200) of fresh unfiltered synthetic cases. The
   random-flip rates hold at 1–2% across all four pre-registered
   generator-variant robustness cells.
2. **No mechanism-level mitigation sketched in #351 achieves
   class-level removal; the paired records show boundary events both
   disappearing and appearing.** The quantization deadband (C3) and
   the min-batch-2 variant (C4b) both suppress the *recorded*
   real-data jump (driver-flip response 3.60e-2 px → below 8e-4 px
   in both bands) — yet C3 retains 72–74% of the baseline
   random-flip material rate (combined paired difference −0.53 pp,
   95% cluster-bootstrap CI [−0.95, −0.10]), far above the frozen
   ≥10× gate, and C4b *introduces more new material events than it
   resolves* (e.g. 13 resolved vs 19 introduced, uniform estimand).
   The candidates that meet the tail gate change what is estimated:
   the retention guard (C2b) and convex Huber-IRLS (C5) strongly
   suppress the class (C5 is zero in the reported arenas) at
   1.6–2.5× the baseline truth error. **No candidate passes all
   pre-registered gates**, so per the frozen decision tree the
   upstream proposal is observability-first.
3. **bhawkins's literal batch-removal suggestion is a clean runtime
   result on the recorded case**: 3.61× wall time (73.506 →
   20.377 s), 4.20× fewer refits (38,324 → 9,118), drift
   4.0e-6 px (L) / 5.9e-5 px (P), and a stability profile identical
   to baseline — the runtime motivation it was proposed for in
   isce3#173, now measured on one pinned production-shaped run. No
   material fidelity or stability penalty was detected in these
   arenas.

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

## Baseline sensitivity (C0)

Confirmatory ensemble, 200 seeds, azimuth (L) band:

| estimand | n flips | material rate | response p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| uniform | 2,000 | 0.0170 (34/2,000) | 0 (exact) | 0 (exact) | 1.06e-2 | 2.29e-2 |
| stratified | 2,000 | 0.0215 (43/2,000) | 0 (exact) | 0 (exact) | 1.17e-2 | 2.27e-2 |
| driver | 200 | **0.265** (53/200) | 3.39e-3 | 1.38e-2 | 2.68e-2 | 3.20e-2 |

For the random-flip estimands the empirical p50 and p90 are exactly
zero: 94.65% of uniform (1,893/2,000) and 94.20% of stratified
(1,884/2,000) flips produced *exactly zero* response in both bands,
while the tail reaches several 1e-2 px — a point mass at exact
zero with a rare boundary-crossing tail, consistent with the
discrete-membership mechanism of the original report. (Per-flip removal
chains are not archived, so the zero responses are reported as
observed outcomes, not as a mechanism claim.) The driver distribution's p50 and p90 are non-zero (54/200 driver
flips still produce exactly zero response). Baseline retention: median
3.7% (the ~4% high-weight-elite regime of the recorded production
case reproduces on fresh seeds).

## The frontier: jump damping vs estimator fidelity

Confirmatory ensemble (seed medians; material rates pooled over
flips; azimuth band; ESS = Kish effective sample size, defined for
C5 only):

| cand | material U | material S | driver p99 L | drift p50 L | truth p50 L | retention p50 | ESS p50 | gates failed |
|---|---|---|---|---|---|---|---|---|
| C0 | 0.0170 | 0.0215 | 2.68e-2 | 0 | 1.21e-2 | 0.037 | - | g1 |
| C3 | 0.0125 | 0.0155 | 2.56e-2 | 4.8e-3 | 1.17e-2 | 0.040 | - | g1 |
| C4 | 0.0170 | 0.0215 | 2.68e-2 | 0 † | 1.21e-2 | 0.037 | - | g1 |
| C4b | 0.0200 | 0.0240 | 2.67e-2 | 2.3e-3 | 1.22e-2 | 0.036 | - | g1 |
| C4+C3 | 0.0095 | 0.0155 | 2.74e-2 | 5.2e-3 | 1.17e-2 | 0.040 | - | g1 |
| C2b | 0.0005 | 0.0000 | 6.4e-3 | 2.7e-2 | 2.63e-2 | 0.250 | - | g3 |
| C5 | 0.0000 | 0.0000 | 3.1e-3 | 3.3e-2 | 3.05e-2 | 1.0 | 384.8 | g3 |
| C6 | 0.0000 | 0.0000 | 2.1e-3 | 2.8e-1 | 2.88e-1 | 1.0 | - | g2,g3,g4 |

† C4 is bit-identical to C0 on this arena by construction
(`n = 900 < 2500` ⇒ k ≡ 1) — carried as the pre-registered
runtime/degeneracy control.

Seed-paired material-rate differences vs C0 (percentage points,
95% seed-cluster bootstrap CI, B = 10,000, fixed bootstrap seed):

| cand | uniform | stratified | combined U+S | driver |
|---|---|---|---|---|
| C3 | −0.45 [−1.00, +0.10] | −0.60 [−1.15, −0.05] | −0.53 [−0.95, −0.10] | −5.5 [−11.5, +0.5] |
| C4b | +0.30 [−0.30, +0.85] | +0.25 [−0.35, +0.85] | +0.27 [−0.18, +0.73] | +2.5 [−3.5, +8.5] |
| C4+C3 | −0.75 [−1.35, −0.15] | −0.60 [−1.25, +0.05] | −0.68 [−1.15, −0.18] | −3.0 [−10.5, +4.5] |
| C2b | −1.65 [−2.25, −1.10] | −2.15 [−2.75, −1.60] | −1.90 [−2.38, −1.45] | −26.0 [−32.5, −20.0] |
| C5 | −1.70 [−2.30, −1.15] | −2.15 [−2.75, −1.60] | −1.93 [−2.38, −1.47] | −26.5 [−32.5, −20.5] |

No equivalence margin was pre-registered, so no equivalence claim is
made; the operative comparison is against the frozen ≥10× reduction
gate, which C3 (retaining 72–74% of the baseline rate), C4b and
C4+C3 all miss by an order of magnitude.

Paired material-event transitions vs C0 (persistent / resolved /
introduced):

| cand | uniform | stratified | driver |
|---|---|---|---|
| C3 | 15 / 19 / 10 | 22 / 21 / 9 | 29 / 24 / 13 |
| C4b | 21 / 13 / **19** | 28 / 15 / **20** | 36 / 17 / **22** |
| C4+C3 | 9 / 25 / 10 | 14 / 29 / 17 | 21 / 32 / 26 |
| C2b | 0 / 34 / 1 | 0 / 43 / 0 | 0 / 53 / 1 |
| C5 | 0 / 34 / 0 | 0 / 43 / 0 | 0 / 53 / 0 |

The mechanism-level candidates resolve some boundary events and
introduce others (C4b introduces more than it resolves); the
estimator-changing candidates resolve essentially all of them. The
frontier is sharp: the hard-deletion family (C0/C3/C4/C4b/C4+C3)
occupies the high-accuracy/low-stability corner — truth error
1.17–1.22e-2 px, material rates 1–2.4% — while C2b and C5 buy
class-level suppression at truth-error inflation of 2.17×/1.63×
(C2b, L/P) and 2.52×/2.16× (C5), because they retain junk influence
the baseline purges. C6 (no rejection at all) anchors the extreme:
stable and 24× the truth error.

## Recorded 40k case study

Container, production kwargs, recorded GPU-baseline samples; the
recorded driver flip (row 22961, −1/32 px azimuth):

| cand | stop | retention | refits | seconds | drift RMS L / P | driver-flip RMS L / P | material |
|---|---|---|---|---|---|---|---|
| C0 | w_test | 0.0419 | 38,324 | 73.5 | — | **3.599e-2** / 7.57e-3 | yes |
| C3 | w_test | 0.0526 | 37,896 | 107.0 | 2.66e-3 / 2.01e-3 | 9.58e-5 / 7.92e-4 | no |
| C4 | w_test | 0.0419 | 9,118 | **20.4** | 4.03e-6 / 5.93e-5 | **3.599e-2** / 7.52e-3 | yes |
| C4b | w_test | 0.0419 | 7,457 | 22.5 | 4.03e-6 / 5.93e-5 | 1.41e-5 / 1.86e-5 | no |
| C4+C3 | w_test | 0.0527 | 7,242 | 23.1 | 2.64e-3 / 2.00e-3 | 1.71e-4 / 1.49e-4 | no |
| C2b | max_rejections | 0.25 | 30,001 | 96.6 | 1.43e-2 / 1.60e-2 | 6.68e-5 / 2.27e-5 | no |
| C5 | irls_converged | 1.0 | 11 | 0.04 | 1.44e-2 / 1.56e-2 | 1.54e-5 / 8.3e-9 | no |
| C6 | max_refits | 1.0 | 1 | 0.02 | 9.42e-2 / 9.75e-1 | 1.41e-5 / 0 | no |

Two observations that must be read together:

- **On this one case, C3 and C4b suppress the recorded CPU-vs-GPU
  product difference** (response below 8e-4 px in both bands) at
  2.7e-3 / 4.0e-6 px azimuth drift — case-level suppression.
- **The ensemble shows it is not class-level removal**: the same
  candidates leave the fresh-seed material rates near baseline
  (C4b above it), and the paired transitions show sampled boundary
  events being both resolved and introduced rather than uniformly
  eliminated. Batch removal moves
  membership boundaries to the kth/(k+1)th cutoff and the deadband
  moves the stop surface.

The literal-C4 row doubles as the runtime measurement: 3.61× wall
(73.506 → 20.377 s), 4.20× fewer refits, drift 4.0e-6 px (L) /
5.9e-5 px (P), stability profile identical to baseline (the endgame
is sequential below n = 5,000 under the frozen current-n/floor
reading — the recorded driver is removed with ~3,435 samples left).
A single pinned run; no material fidelity or stability penalty was
detected in these arenas.

## Robustness block (exploratory, 50 seeds per cell)

C0's uniform/stratified material rates hold at 1–2% in all four
generator variants (quantizer phase shifted by q/2; driver at the
far corner; elite fraction halved / doubled). The driver-node rate
tracks the elite size strongly — 0.46 at elite fraction 0.04, 0.06
at 0.16 (fewer elite ⇒ each high-weight node more decisive). C3 and
C4b fail to remove the class in every cell (C4b reaches 0.56 driver
rate in the elite-low cell); C2b and C5 keep it strongly suppressed
(≤0.02). Termination health across the campaign is clean: zero
caught execution errors and zero rank failures in the 70,400
synthetic mirror fits (400 seeds × 22 fits = 8,800 per candidate ×
8 candidates) plus the 16 case-study fits; every confirmatory flip
fit stopped by the w-test or the candidate's own guard/convergence
rule (C6 terminates at `max_refits` by design and fails gate g4
accordingly).

## Always-reported diagnostics (base fits)

Aggregates of the pre-registered per-fit diagnostics (confirmatory,
seed medians; the flip fits archived only response/material/stop
reason, so the per-fit diagnostics below cover base fits only — a
deviation from the pre-registration's per-fit wording that cannot
be reconstructed from the frozen archive): normal-matrix condition
number 8.5e2–1.1e3 across all
candidates (final-iterate values; ~3 digits of float64's ~16
consumed), final-inlier bbox coverage 0.965 for the hard-deletion
family and ≈1.0 for the high-retention candidates, final-batch
"overshoot" (stop-compliant rows removed in the final batch) at most
1 for the k=1 candidates and at most 2 for C4b across all
confirmatory seeds — nonzero because the stop test is componentwise
while removal ranks the combined score, the very asymmetry C3's
eligibility rule addresses — C5 Kish ESS median 384.8/900 (42.8%;
retention 1.0 by construction — different quantities, reported
separately; median 817/900 samples downweighted, median smallest
Huber factor 0.034), minimum-quadrant inlier count median 6 for the
hard-deletion family (all four quadrants stay populated), and zero
ridge fallbacks in any base fit (flip-level counts were not
archived). Exact blocks per candidate are in `summary.json`.

## Pre-registered decision

Gate evaluation (section 7) on the confirmatory ensemble + 40k case
(g4 covers base and flip terminations):

| cand | g1 tail-flip | g2 drift | g3 truth | g4 termination | g5 runtime | all |
|---|---|---|---|---|---|---|
| C3 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4b | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C4+C3 | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| C2b | ✔ | ✔ | ✘ | ✔ | ✔ | ✘ |
| C5 | ✔ | ✔ | ✘ | ✔ | ✔ | ✘ |

None of the behavior-changing candidates satisfied all five
pre-registered gates: C3, C4, C4b and C4+C3 failed the frozen ≥10×
material-tail gate, while the two candidates that met that gate,
C2b and C5, failed the truth-error gate. Following the
pre-registered scope rule (frozen at `d234d13`, before results), we
therefore do not propose a behavior-changing change from this
experiment. **The first upstream step is observability-only** — the
instrumentation of this study (stop-reason taxonomy, retention /
inlier-fraction reporting next to the coefficients the workflow
already logs, condition/coverage diagnostics) plus the noisy-input
retention test promised in the #351 follow-up. This instrumentation
does not mitigate the discontinuity; it exposes extreme retention
and termination behavior while the behavior-changing direction
remains open. Two data-supported side notes: literal C4 qualifies
on its original runtime motivation independently of the stability
question, and `crit_value` recalibration remains an *unevaluated*,
cal/val-controlled lever with adjacent sensitivity evidence (the
recorded 160× response drop between crit 0.1 and 0.2); because C1
was not part of the confirmatory roster, that is a follow-up
hypothesis, not an identified unique root lever.

## Limitations

- The synthetic generator is a two-population idealization
  calibrated on one production pair; the robustness block varies its
  structure but not its family.
- The 40k case study is one recorded pair (single dataset, single
  environment); its numbers — including the 3.61× runtime — are
  single-run case evidence, not estimates.
- Material-jump rates are pooled over flips; per-seed counts and the
  seed-cluster bootstrap intervals above are in the artifacts
  (n = 200 seeds ⇒ worst-case ±7 pp at 95% on a binary rate).
- No equivalence margin was pre-registered; statements about
  candidates "retaining" the baseline rate are ratio statements
  against the frozen gate, not equivalence claims.
- Provenance: the scheduled run spanned two reported HEADs — 72 of
  400 seed records report `6350809` (31 clean / 41 dirty) and 328
  report `ee802b2` (323 clean / 5 dirty). The only committed delta
  between them is the results aggregator and its tests, and the
  tracked generator/protocol blobs are identical across both
  commits (blob IDs in `summary.json`'s
  `provenance_distribution`). However, 46 records report
  `worktree_dirty=true` and the dirty paths or diffs were not
  captured, so their exact worktree states cannot be reconstructed.
  We treat this archive as a single scheduled run with identical
  tracked generating inputs, **not** as a fully clean, commit-pinned
  run. The provenance distribution covers the 400 seed records;
  `real40k.json` carries its own provenance block.

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
