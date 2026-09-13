# Correction (2026-09-13) — `correlation_peak` spread in the 9-run table

## What was wrong

`README.md` and `reports/2026-08-cpu-insar-run-to-run-reproducibility.md`
stated that `correlation_peak` "differs at max **7.15e-07**" across the
9 standalone `dense_offsets` runs, and read its match with the 08-16 E2E
bundle's `correlationSurfacePeak` `max|d|=7.15256e-07` as the isolated step
and the pipeline "landing on the same number".

That figure is the `idle` rep1-vs-rep3 value from `RESULTS_PHASE_C.md`
(line 99). It is a **within-group** pair value; it was mis-recorded as the
9-run maximum. The 08-16 match is real, but it means the 08-16 E2E pair also
stayed inside the dominant group — it is a confirmation of the within-group
scale, not evidence of an upper bound.

Caught during the VECR review round of 2026-09-13 (issue reframing for
bench#48); recounted independently by two reviewers with agreement to all
printed digits.

## Recount — all 36 run pairs

Grouping = exact sha256 of `snr` and `covariance` (both change only through
the raw-correlation stage). Threshold-based "equivalence" is not transitive,
so the grouping is by exact hash, not by a difference threshold.
Script: `harness/recount_pairs.py` (reads the 9 replicate directories
read-only).

| | value |
|---|---|
| groups | G0 = {idle1, idle2, idle3, load1, omp1_2, omp1_3}; G1 = {load2}; G2 = {load3}; G3 = {omp1_1} |
| within-group pairs (15, all inside G0): `dense_offsets` max | 0.03125 px (= 1/32 px) |
| within-group: `correlation_peak` max | 7.7486038208007812e-07 |
| within-group: fraction of `correlation_peak` pixels that differ | 72.6433 % – 85.3608 % |
| between-group pairs (21): `dense_offsets` max | 63.5625 px |
| between-group: `correlation_peak` max | 0.31626671552658081 |
| between-group: offset components with \|Δ\| > 1 px | 317 – 360 of 766,270 |
| between-group: match positions with \|Δ\| > 1 px (either component) | 183 – 197 of 383,135 |
| between-group: `correlation_peak` pixels with \|Δ\| > 1e-3 | 37 – 68 |

`correlation_peak` over the scene spans [-0.046, 1.057] with mean 0.313, so
a between-group change of 0.316 is a different match at that position, not
a rounding difference; 63.6 px is the search-window scale (half-search 32).

## What is unaffected

- `dense_offsets` and `correlation_peak`: still **9 distinct values in
  9 runs** (`RESULTS_PHASE_C.md`).
- The two-scale description (1/32 px within the dominant group, tens of px
  outside it) was already correct; only the `correlation_peak` maximum and
  its reading were wrong.
- The bench#48 intervention A/B (`artifacts/fftw-intervention-ab-20260828/`)
  used its own control replicates and its conclusions do not depend on the
  corrected figure.
- Raw replicate data, the frozen `PREREGISTRATION.md`, `AMENDMENT_A1.md`
  and all `RESULTS_PHASE_*.md` are unchanged.
