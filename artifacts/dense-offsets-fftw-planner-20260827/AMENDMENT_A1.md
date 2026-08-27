# bench#36 Step 2 — Amendment A1 to the pre-registration

Written 2026-08-27, **after** seeing C-idle / C-load / C-omp1 rep1, and
**before** running any additional replicate. `PREREGISTRATION.md` itself is
unmodified (sha256 c8883a3df81b77cc7291280731d9b99f9acd117b63235326c3ec688539097ad0).

## Why an amendment is needed

The pre-registered Phase C decision rule 4 reads:

> C-omp1 vs C-idle: any difference in *output* falsifies A0-2 and revives H4.
> Runtime is also recorded; A0-2 predicts no systematic runtime difference either.

That rule was written on the assumption -- untested at the time -- that the
baseline would be reproducible, so that any C-omp1 vs C-idle difference could
be attributed to `OMP_NUM_THREADS`. **C-idle falsified that assumption**: the
idle arm produced 3 distinct results in 3 runs. With a nondeterministic
baseline, "C-omp1 rep1 differs from C-idle rep1" is exactly what the null
hypothesis also predicts, so the rule as written cannot discriminate and
**must not be applied**.

Observed (recorded before this amendment was written):

- C-idle (3 reps): `dense_offsets` differs at max 3.125e-02 = 1/32 px;
  `snr` and `covariance` byte-identical across all three.
- C-load (3 reps): `dense_offsets` differs at max 6.0e+01 px; `snr` and
  `covariance` also differ.
- C-omp1 rep1 vs C-idle rep1: `dense_offsets` max 6.06e+01 px, `snr` and
  `covariance` differ -- i.e. the *load-like* pattern, not the idle pattern.
- Wall times: idle 519 / 564 / 551 s; load 1139 / 1141 / 1199 s;
  omp1 701 s. omp1 is ~29% slower than the idle mean (545 s), outside the
  idle arm's own spread. **A0-2 predicted no runtime difference; this
  prediction failed.**

## What is amended

1. **Rule 4 is withdrawn as unusable.** It is replaced by a within-arm test:
   run C-omp1 to 3 replicates (2 more) and compare *within* the omp1 arm,
   against the within-arm signatures already measured for idle and load.
   The discriminating signature is not "differs / does not differ" but
   **which stage's plan moved**:
   - idle signature: `snr` + `covariance` identical, `dense_offsets` bounded
     by the 1/32 px oversampling quantum -> only the oversampled-stage plan
     varied.
   - load signature: `snr` + `covariance` differ, `dense_offsets` reaching
     tens of pixels -> the raw-correlation-stage plan varied too, moving
     integer argmax decisions.

   Reading: if the omp1 arm's *within-arm* signature matches idle, then
   `OMP_NUM_THREADS` did not change the character of the nondeterminism and
   A0-2's output claim survives. If it matches load, `OMP_NUM_THREADS`
   systematically changes which stage destabilises, and A0-2's output claim
   needs revision.

2. **The runtime prediction in A0-2 is recorded as FAILED**, independent of
   how (1) resolves. `OMP_NUM_THREADS=1` measurably slows this step (~29% on
   one replicate). The source-level claim that `pycuampcor`'s CPU Ampcor
   contains no OpenMP and no threading is a fact about the source and is
   unaffected; but it does **not** follow that `OMP_NUM_THREADS` is inert for
   the `dense_offsets` *step*, which also does GDAL raster copies and other
   library work. The mechanism of the slowdown is **not established** and is
   not claimed here.

## Stopping rule for the extension

3 replicates total in the omp1 arm. No further extension without a further
written amendment. If the within-arm signature is ambiguous at n=3, that is
reported as ambiguous.
