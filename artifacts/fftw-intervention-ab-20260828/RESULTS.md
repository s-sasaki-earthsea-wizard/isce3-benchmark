# bench#48 — FFTW planner intervention A/B — results

Run 2026-08-28, 06:11–08:43 UTC, host nucbox-evo-t1, per
`PREREGISTRATION.md` (freeze commit `040c000`). All arms built and run in
pre-registered order; no replicate was discarded. One post-freeze harness
fix (in-container `reference.slc` hash+delete, commit `68ea161`) touched
mechanics only.

## Headline

**The causal loop is closed.** Replacing `FFTW_MEASURE` at the 7
`pycuampcor` planner sites stops the run-to-run divergence completely, on
both scales at once:

- **control** (`FFTW_MEASURE`, stock policy): 3 runs → **3 distinct
  results** on `dense_offsets`, `snr`, `covariance`, `correlation_peak`
  (within-arm max |Δ| up to **63.4 px** on `dense_offsets`;
  `gross_offsets` identical). Plan hashes: **3 distinct in 3 runs** — the
  plan↔output correlation is now visible per run.
- **arm A** (`FFTW_ESTIMATE`): 3 runs → **byte-identical** on all five
  layers. But wall cost **+7.0%**.
- **arm B** (`FFTW_MEASURE` + pinned wisdom + `FFTW_WISDOM_ONLY`): 5 runs
  → **byte-identical** on all five layers, plan hash identical in 5/5,
  wall **−1.2%** (statistically: no cost).

**Selected arm per the frozen decision rule: arm B.** Arm A passed the
truth gate but failed the ≤ 1.05× cost gate; arm B passed both.

## Wall times (`Elapsed (wall clock)`, /usr/bin/time -v)

| arm | reps (s) | median (s) | vs control |
|---|---|---|---|
| control | 634.27, 616.99, 621.78 | 621.78 | — |
| arm A | 679.22, 658.27, 665.04 | 665.04 | **+6.96%** |
| arm B (n=5) | 620.34, 615.64, 602.70, 614.34, 595.88 | 614.34 | **−1.20%** |
| wisdom generator (MEASURE + export) | 657.19 | — | (one-off) |

Arm A vs control ranges do not overlap (658.3–679.2 vs 617.0–634.3): the
ESTIMATE slowdown is real at n=3, not noise. Per the frozen rule the n=5
extension applied to the selected arm only, so arm A's +7.0% is an n=3
figure.

## Plan construction time (probe, sum over the 12 planner calls)

| arm | per-run total |
|---|---|
| control (MEASURE) | ≈ 0.50 s |
| arm A (ESTIMATE) | ≈ 0.004 s |
| arm B (wisdom import + WISDOM_ONLY) | ≈ 0.002 s |

Planning cost is negligible against ≥ 595 s walls in every arm — the
+7.0% of arm A is **plan quality in execution**, not planning overhead.
This is the amortisation effect the review predicted: plans are built
once and executed for 545 chunk rows, so `FFTW_MEASURE`'s better plans
pay for themselves many times over, and arm B keeps exactly that benefit
while pinning which plan is used.

## Truth gate detail

- Within-arm sha256 (5 layers): control `3uniq/3` on four layers
  (`gross_offsets` `1uniq/3`); arm A `1uniq/3`; arm B `1uniq/5`.
- Control within-arm differences reproduce the Step 2 signature:
  `dense_offsets` diffs at n≈400–419/766270 with max ≈ 60–63 px (the
  excursion scale), plus near-ULP spreads on `snr` /
  `correlation_peak` / `covariance` (the fine scale). Both vanish
  simultaneously in the deterministic arms, as the single-mechanism
  attribution predicts.
- Cross-arm: arm A and arm B outputs differ from any given control
  realisation (each pins a *different* valid plan set) — expected;
  the target was within-arm reproducibility, not matching one MEASURE
  draw.
- Input-side check: `reference.slc` sha256 identical across **all 11
  measured runs + generator** (`6b23416a…`).

## Predictions scorecard (§7 of the pre-registration)

- **P1 hit** — control failed the truth gate at n=3.
- **P2 hit** — arm A passed; ESTIMATE planning ≈ 4 ms vs MEASURE ≈ 0.5 s.
- **P3 resolved** — the deliberately unpredicted direction came out
  **slower**: +6.96%, beyond the +5% gate.
- **P4 half-held** — per-rep wisdom exports are byte-identical to *each
  other* (5/5, `36242619…`) but not to the generator file (`8621e7ba…`):
  same 12117 bytes, different serialisation order. Functional identity
  is established by zero NULL plans under `FFTW_WISDOM_ONLY` plus
  identical outputs; byte-stability of the *export* holds within the
  pinned process, not across import/re-export.
- **P5 hit** — probe overhead invisible (control walls sit inside the
  Step 2 background range).
- **P6 hit** — exactly 12 planner calls per run
  (2× cuFreqCorrelator·3 + 2× C2C·2 + 1× R2R·2).
- The arm B alignment-mismatch failure mode did not occur.

## Deviations and incidents

- **D-1** (declared at freeze): control carries the env-gated
  observation probe; arms differ only in planner flags.
- **Harness fix post-freeze** (`68ea161`): `reference.slc` hash+delete
  moved into the container after host-side `rm` hit Permission denied
  (Step 2 gotcha). ctrl reps 1–3 hashes were recorded host-side before
  the fix; values match the in-container records of later reps.
- **No rebuild before the n=5 extension**: the pre-registration's
  rebuild clause was conditional on the shared build tree having moved;
  it had not (still `d63c470a2`, clean). Verified before reps 4–5.

## Reading for the upstream issue

1. The regression-style claim is now interventional: **stock
   `FFTW_MEASURE`-without-wisdom is the cause** of CPU Ampcor's
   run-to-run irreproducibility; removing it removes the effect,
   9/9-style divergence → byte-identity.
2. **`FFTW_ESTIMATE` is the simple fix but not free**: +7.0% on the
   `dense_offsets` step on this host/dataset (≈ +43 s of ≈ 622 s). On
   the full CPU INSAR pipeline measured 08-16 (6178.6 s), that step
   share prices the same delta at well under 1% end-to-end — worth
   stating both numbers.
3. **Pinned wisdom keeps MEASURE quality at zero marginal cost**, but
   productising it (where the wisdom file lives, when it is generated,
   process-global interactions with `cxx/isce3/fft`) is an upstream
   deployment decision; our arm B is a PoC that bounds the attainable
   result, not a shippable design.
4. All four review corrections from bench#48 apply unchanged (conditional
   reproducibility of pinned wisdom; ESTIMATE-uses-imported-wisdom scoping;
   wisdom as process-global state; no user-side workaround exists).
