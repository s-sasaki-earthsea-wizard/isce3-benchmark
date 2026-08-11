# Rubbersheet polyfit: a one-quantum input flip can be amplified by an endgame membership change

- Date: 2026-08-11
- Host: NucBox EVO T1 (CPU-only workload; OMP/OPENBLAS/MKL pinned to 1
  for all judged fits, cross-thread control included)
- isce3 commit: `2919e1c97` (develop, `isce3-benchmark:dev` from-source
  build 0.26.0-dev) for the container verification; host runs import
  `isce3/math/offsets_polyfit.py` from the surrounding checkout — the
  module is byte-identical on develop, v0.25.16 and the current work
  branches (last touched by upstream PR #173, `4f48a8a98`)
- isce3-benchmark commit: this branch (`feat/polyfit-membership-repro`)
- Runconfig: none (module-level study; the fit kwargs are the values
  `nisar.workflows.rubbersheet.run_rubbersheet_with_polyfit` passes to
  the fit, rebuilt from the RIFG/RSLC files — incl. the production
  default `critical_value: 0.1` of `share/nisar/defaults/insar.yaml`)
- Dataset: NISAR L-SAR sample pair, ascending track 139 frame 019
  (L1 RSLC), processed once with the CPU and once with the GPU InSAR
  workflow; per-input md5s recorded in
  [`replay_real40k.json`](../artifacts/polyfit-membership-20260811/replay_real40k.json)

**Status**: evidence consolidation for
[issue #26](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/26).
The investigation itself ran in a private working repo; this report
imports the recorded results into their public home
([`artifacts/polyfit-membership-20260811/`](../artifacts/polyfit-membership-20260811/)),
adds a self-contained minimal reproducer to `scripts/`, and verifies
it in this repository (host + dev container). The upstream issue
draft is the next step and is intentionally **not** part of this
report.

**TL;DR**: the CPU and GPU InSAR workflows produced RIFG
`pixelOffsets` layers that differ by a smooth ~3.6e-2 px RMS
degree-2 bowl (a linear regression on the offsets difference
explains ~99.5% of the phase-difference variance). In a controlled
replay of the production rubbersheet fit
(`isce3.math.offsets_polyfit.polyfit_offsets`, sequential
worst-outlier removal, default `crit_value=0.1`), transplanting
**one** isolated CPU-Ampcor sample — a high-correlation window
whose subpixel peak differs by exactly one correlation-grid quantum
(1/32 px) — into the GPU baseline is **necessary and sufficient**
to reproduce the observed coefficient difference to the available
production log precision. The amplification is discontinuous: the
removal chain forks near its end and the final inlier set — the set
that defines the fit — changes wholesale. The mechanism is fully
reproduced by a 900-sample self-contained synthetic
(`repro_polyfit_quantum_membership.py`, 8/8 checks PASS on both the
host and the dev container).

## 1. Why the fit can jump: two-stage amplification

1. **Quantizer (Ampcor subpixel argmax).** The correlation-surface
   argmax rectifies float-epsilon differences into "exactly 0 or at
   least one quantum (1/32 px)". In the controlled sample sets
   extracted on the production sample grid, two independent
   CPU-Ampcor runs were each compared against the GPU-Ampcor
   baseline: 39,990 of the 40,000 sample rows are bit-identical in
   the first comparison (10 rows differ; the second run differs in
   9 rows; the union is 12 changed rows, 7 common to both), and
   every differing offset differs by at least one quantum.
2. **Membership amplifier (sequential worst-outlier removal).** Each
   iteration refits and removes the worst standardized residual;
   flipping a single removal decision re-routes the rest of the
   chain. The fit response to an input offset is piecewise-smooth:
   linear in the offsets while the removal chain is unchanged (and
   exactly constant in a perturbed sample's value once that sample
   is purged), with discontinuous jumps at membership boundaries.
   Chains fork from perturbations as small as ~1e-4 px, but almost
   all forks re-converge benignly; across a membership boundary the
   jump is neither proportional nor monotone in the perturbation
   (3.6e-2 px from one quantum on the real data, vs a ~1e-5 px
   continuous-response scale).

The structural condition is one inequality. The exact w-test stop
tolerance at a sample is `crit_value * sigma * sqrt(1/w^2 - h_ii)`,
bounded above by `crit_value * sigma / w`. At the driver window's
weight even the bound is small:

```
crit_value * sigmaL / w  =  0.1 * 0.1247 / 0.9485  =  0.0132 px
   <  q/2  =  1/64  =  0.0156 px
   (half-bin bound on the nearest-grid quantization error)
```

so a sufficiently high-weight sample can fail the stop test on
quantization error alone. This is a property of the high-weight
tail, not of the whole elite — at the survivor-median weight 0.56
the bound is 0.0223 px and the inequality reverses. It does not by
itself force a deep purge; the observed production behavior (95.8%
of the 40k samples removed, the fit decided by a ~4% elite with
median peak 0.56) shows the susceptibility was realized on this
data. The `crit_value` sweep confirms the specificity: at 0.2 the
flip response drops 160-fold (2.2e-4 px), and by 0.5-2.0 it is at
the 1e-5 px continuous-response floor.

## 2. Evidence chain (recorded artifacts)

L1 — controlled replay of the production fits (real 40k samples):

| Result | Value |
|---|---|
| Self-consistency gate | replayed fit reproduces the on-disk culled surfaces to 6.1e-15 px and the logged coefficients to print precision (4.1e-9) |
| Determinism controls | A/A bit-identical; OMP=1 vs 16 bit-identical; traced mirror bit-equivalent |
| Full-input swap (CPU raw offsets into the GPU baseline) | reproduces the observed CPU-minus-GPU coefficient target: cosine 1-6e-15, residual field 9e-9 px vs a 3.6e-2 px target; the replayed coefficients match the CPU run's logged values directly at max |d| 5.0e-8 |
| Channel attribution | offsets-only = full target; weights-only (38.5k float32 epsilons) forks at iter 468 and re-converges benignly (2.4e-9 px) |
| Minimal destructive set | ONE sample row: 22961, corr_peak 0.9485, dAz exactly -1/32 px — necessary (complement transplants = exactly zero) and sufficient (single transplant = full target) within the observed difference sets; the other 11 changed rows of the 12-row union, including a 5.6/13.9 px (azimuth/range) outlier row, change nothing |
| Perturbation basin at the driver | only the exact -1/32 px value lands in the target basin; -1e-5 doesn't fork, -1e-4 / sub-quantum / ±2-quantum deltas fork benignly |
| Endgame membership | final inliers 1,677 (GPU baseline) vs 1,535 (CPU-offsets), 985 common; driver removed at iteration 36,565 of 38,465 (95.1% of the chain); amplification curve stays at 1e-5..1e-3 px mid-run and explodes over the last ~2,000 iterations |

The endgame-membership figures (final inlier counts per chain,
pairwise common inliers and first divergences, the driver's removal
iteration) are recorded in
[`membership_summary_real40k.json`](../artifacts/polyfit-membership-20260811/membership_summary_real40k.json),
derived from the recorded removal sequences by the harness
`membership` subcommand (aggregate counts only).

L0 — minimal synthetic reproducer (public, this repo): a 30x30
two-population case (coherent elite + junk majority, 1/32 px
quantization, float32 storage path, production kwargs) with one
driver sample at the grid node nearest the real driver's radar
position carrying the real 0.9485 peak. Pinned seed 29:

| Check | Synthetic-900 | Real 40k |
|---|---|---|
| Baseline retention | 3.6% (868/900 removed), driver survives | 4.2% (38,323/40k removed), driver survives |
| Flip -1/32 px at the driver | removed at iteration 832/870 = 95.6% of the chain | removed at iteration 36,565/38,465 = 95.1% |
| First fork | iteration 83 | iteration 1,321 (single-transplant probe) |
| Final membership | 32 vs 30 inliers, 25 common | 1,677 vs 1,535, 985 common |
| Induced azimuth jump | RMS 2.75e-2 px, max 0.106 px | RMS 3.60e-2 px |
| Tolerance bound vs half-quantum | 0.0132 px < 0.0156 px | same (production kwargs) |

The pinned seed is an **existence proof** found by a documented
40-seed hunt (22/40 baseline-qualify, 7/40 pass every flip
criterion; `minrepro_hunt40.json`); no prevalence claim is made.

## 3. Verification in this repository

`scripts/repro_polyfit_quantum_membership.py` (standalone; runs in
any isce3 Python environment — NumPy, and SciPy transitively via
the module under test) and the `minrepro` subcommand of
`scripts/polyfit_sensitivity.py` were both run at the destination:

| Environment | isce3 | numpy | Checks | Discrete chain | Float tails |
|---|---|---|---|---|---|
| Host (file import from checkout) | `e53666f94` work branch (module = develop) | 2.2.6 | 8/8 PASS | identical | reference |
| `isce3-benchmark:dev` container | 0.26.0-dev+`2919e1c97` from-source | 1.26.4 | 8/8 PASS | **identical** (868/870 removals, driver at 832, fork at 83, 32/30/25 membership) | coefficients differ by up to ~2 ULP |

The discrete outcome was identical in the two tested software
environments on the same host; coefficient tails differed by up to
about 2 ULP (numpy/BLAS version difference). Other CPU
architectures and BLAS implementations are untested. Bit-level
cross-checks: the ported harness and the standalone script
reproduce the private repo's recorded artifact **bit-identically**
on the same host (coefficients and coefficient deltas equal as
printed JSON). The `probe` subcommand was validated against the
recorded probe JSONs on the real data (peak-only 1.883063e-12 px
and pair 3.599102e-02 px reproduced to all printed digits).

The fit kwargs are pinned to the values the workflow actually
passes (abw 1263.68013808518, rsr exactly 48 MHz, rebuilt from the
RIFG/RSLC files); an earlier near-production constant pair
(relative difference ~3e-7 in sigmaL, ~2.7e-5 in sigmaP) was
retired during review — re-judging the pinned case under the
corrected values changed no removal decision, so the recorded
chain, membership and coefficients are unchanged bit-for-bit (the
sigmas enter only the w-test decisions).

Frozen run records: `repro_run_host.{txt,json}`,
`repro_run_container.{txt,json}` in the artifact directory.

## 4. Mitigation option space (for the upstream discussion)

Recorded from the team review for the upcoming issue — to be
presented as **options with a question, not a pre-made PR** (design
judgment belongs to the maintainers):

1. **Confirm and calibrate the `crit_value` semantics** — the
   question we most want answered. Classical w-test critical values
   are typically of order unity to several, depending on the
   significance design (e.g. Baarda's B-method); is `crit_value =
   0.1` intended as a standardized-statistic threshold, or was
   "0.1 px" the intent?
2. **Observability and guardrails** — report `n_removed` / the
   inlier fraction in the product log, warn on extreme retention,
   optionally a max-rejection / min-inlier policy. Detection, not a
   cure; a min-inlier cap trades off stopping before the w-test
   criterion is met.
3. **Quantization-aware residual model / deadband** — note the
   numbers: adding q²/12 to the prior variance alone moves sigmaL
   from 0.124705 to 0.125031 px (+0.26%) and the driver tolerance
   bound from 0.013148 to 0.013182 px — still below q/2 =
   0.015625 px, so it is nearly ineffective by itself here. An
   explicit tolerance floor at q/2 (deadband on the stop test) is
   the variant that bites; the crit-0.2 sweep point (160-fold
   response drop) is adjacent empirical support.
4. **Batch / stable trimming** instead of one-at-a-time hard
   deletion — reduces the sensitivity of the chain to a single
   flipped decision.
5. **Comparative evaluation of smooth robust losses** (convex
   Huber-class IRLS): a unique convex solution avoids hard
   membership deletion and is expected to remove this jump class,
   but is a broad behavior change; Tukey-style non-convex losses
   can retain basin sensitivity, so no blanket guarantee is
   claimed — this is an evaluation candidate, not a promise.

## 5. Scope and limitations

- Single dataset, single pair; replay determinism claims are
  same-environment claims. "Exact" means exact to the available
  production log precision.
- **Actual-run reservation (L2a)**: the L1 evidence is a controlled
  substitution on recorded rasters. The actual production CPU run's
  raw offsets were not reconstructed; "the one flip existed in the
  actual CPU run" is strongly supported (the replayed coefficients
  land on the CPU run's logged values at 5.0e-8) but not directly
  observed.
- Pairwise interaction of the 12 changed rows was not enumerated
  (the complement probes close the necessity question for the
  observed sets).
- The synthetic two-population weight structure is a simplification
  of the production corr_peak distribution, not a quantitative
  match (qualitatively consistent: the real survivor-median peak
  0.56 falls inside the synthetic coherent range 0.3-0.8).
- The origin of the one-quantum argmax flip (why the CPU and GPU
  correlation surfaces rank neighboring quanta differently in that
  one window) is out of scope here.
- The synthetic-900 case proves the mechanism exists at small N
  under production kwargs; it does not claim production data always
  sits on such a boundary — on the contrary, 11 of the 12 real
  flipped nodes and all 38.5k weight epsilons were benign.

## 6. Next steps

1. Upstream issue draft (English, isce-framework/isce3): mechanism +
   evidence + the option space above, agreement-before-implementation
   stance. Team review via agmsg, then the user's final review before
   filing.
2. npz redistribution decision (14.5 MB of NISAR-derived 40k sample
   matrices) — deferred to the repo owner; provenance remains
   recorded (input md5s in the replay JSON; the npz SHA-256 and
   array schema are pinned in the artifact README).
3. Cross-link from issue #26 once the assets merge.
