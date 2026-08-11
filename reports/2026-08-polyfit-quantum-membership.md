# Rubbersheet polyfit: one-quantum input flips are amplified by endgame membership changes

- Date: 2026-08-11
- Host: NucBox EVO T1 (CPU-only workload; OMP/OPENBLAS/MKL pinned to 1
  for all judged fits, cross-thread control included)
- isce3 commit: `2919e1c97` (develop, `isce3-benchmark:dev` from-source
  build 0.26.0-dev) for the container verification; host runs import
  `isce3/math/offsets_polyfit.py` from the surrounding checkout — the
  module is byte-identical on develop, v0.25.16 and the current work
  branches (last touched by upstream PR #173, `4f48a8a98`)
- isce3-benchmark commit: this branch (`feat/polyfit-membership-repro`)
- Runconfig: none (module-level study; the fit kwargs replicate
  `nisar.workflows.rubbersheet.run_rubbersheet_with_polyfit`, incl. the
  production default `critical_value: 0.1` of
  `share/nisar/defaults/insar.yaml`)
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

**TL;DR**: the CPU-vs-GPU difference of the RIFG `pixelOffsets`
layer (a smooth ~3.6e-2 px RMS degree-2 bowl, ~99.5% of the observed
unwrapped-phase divergence) is caused by **one** high-correlation
Ampcor window whose subpixel peak differs by exactly one
correlation-grid quantum (1/32 px) between the two runs. The
production rubbersheet fit
(`isce3.math.offsets_polyfit.polyfit_offsets`, sequential
worst-outlier removal, default `crit_value=0.1`) amplifies that
single flip discontinuously: the removal chain forks near its end
and the final inlier set — the set that defines the fit — changes
wholesale. The mechanism is fully reproduced by a 900-sample
self-contained synthetic (`repro_polyfit_quantum_membership.py`,
7/7 checks PASS on both the host and the dev container).

## 1. Why the fit can jump: two-stage amplification

1. **Quantizer (Ampcor subpixel argmax).** The correlation-surface
   argmax rectifies float-epsilon differences into "exactly 0 or at
   least one quantum (1/32 px)". Of the 40k production samples,
   39,990 are bit-identical between the CPU and GPU runs; the 10
   that differ all differ by at least one quantum.
2. **Membership amplifier (sequential worst-outlier removal).** Each
   iteration refits and removes the worst standardized residual;
   flipping a single removal decision re-routes the rest of the
   chain. The output is piecewise-constant in the input: chains fork
   from perturbations as small as ~1e-4 px, but almost all forks
   re-converge benignly. The observed jump requires crossing an
   *endgame membership boundary* — and then the jump size is
   unrelated to the perturbation size.

The structural condition is one inequality. At the driver window's
weight (corr_peak 0.9485) the stop tolerance of the w-test is

```
crit_value * sigmaL / w  =  0.1 * 0.1247 / 0.9485  =  0.0132 px
   <  q/2  =  1/64  =  0.0156 px   (input quantization floor)
```

so the purge cannot stop at the quantization floor of its own input:
it digs into the high-weight elite (95.8% of the production samples
are removed; the fit is decided by a ~4% elite with median peak
0.56) and the final membership sits on a knife edge. The sweep over
`crit_value` confirms the specificity: at 0.2 the flip response
drops 160-fold (2.2e-4 px), and by 0.5-2.0 it is at the 1e-5 px
continuous-response floor.

## 2. Evidence chain (recorded artifacts)

L1 — exact replay of the production fits (real 40k samples):

| Result | Value |
|---|---|
| Self-consistency gate | replayed fit reproduces the on-disk culled surfaces to 6.1e-15 px and the logged coefficients to print precision (4.1e-9) |
| Determinism controls | A/A bit-identical; OMP=1 vs 16 bit-identical; traced mirror bit-equivalent |
| Full-input swap (CPU raw offsets into the GPU baseline) | reproduces the observed CPU-minus-GPU coefficient target: cosine 1-6e-15, residual field 9e-9 px vs a 3.6e-2 px target |
| Channel attribution | offsets-only = full target; weights-only (38.5k float32 epsilons) forks at iter 468 and re-converges benignly (2.4e-9 px) |
| Minimal destructive set | ONE node: sample row 22961, corr_peak 0.9485, dAz exactly -1/32 px — necessary (complement transplants = exactly zero) and sufficient (single transplant = full target); the other 11 flipped nodes, incl. a +5.6/-13.9 px monster, change nothing |
| Perturbation basin at the driver | only the exact -1/32 px value lands in the target basin; -1e-5 doesn't fork, -1e-4 / sub-quantum / ±2-quantum deltas fork benignly |
| Endgame membership | final inliers 1,677 (GPU baseline) vs 1,535 (CPU-offsets), 985 common; driver removed at iteration 36,565 of 38,465 (95.1% of the chain); amplification curve stays at 1e-5..1e-3 px mid-run and explodes over the last ~2,000 iterations |

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
| Stop tolerance vs floor | 0.0132 px < 0.0156 px | same (production kwargs) |

The pinned seed is an **existence proof** found by a documented
40-seed hunt (22/40 baseline-qualify, 7/40 pass every flip
criterion; `minrepro_hunt40.json`); no prevalence claim is made.

## 3. Verification in this repository

`scripts/repro_polyfit_quantum_membership.py` (standalone, numpy +
isce3 only) and the `minrepro` subcommand of
`scripts/polyfit_sensitivity.py` were both run at the destination:

| Environment | isce3 | numpy | Checks | Discrete chain | Float tails |
|---|---|---|---|---|---|
| Host (file import from checkout) | `e53666f94` work branch (module = develop) | 2.2.6 | 7/7 PASS | identical | reference |
| `isce3-benchmark:dev` container | 0.26.0-dev+`2919e1c97` from-source | 1.26.4 | 7/7 PASS | **identical** (868/870 removals, driver at 832, fork at 83, 32/30/25 membership) | coefficients differ by ~2 ULP |

Bit-level cross-checks: the ported harness and the standalone script
reproduce the private repo's recorded artifact **bit-identically**
on the same host (coefficients and coefficient deltas equal as
printed JSON); across numpy 1.26/2.2 the discrete outcome is
unchanged and only float tails move. The `probe` subcommand was
validated against the recorded probe JSONs on the real data
(peak-only 1.883063e-12 px and pair 3.599102e-02 px reproduced to
all printed digits).

Frozen run records: `repro_run_host.{txt,json}`,
`repro_run_container.{txt,json}` in the artifact directory.

## 4. Mitigation option space (for the upstream discussion)

Recorded from the team review for the upcoming issue — to be
presented as **options with a question, not a pre-made PR** (design
judgment belongs to the maintainers):

1. Add the quantization to the noise model:
   `sigma_eff^2 = sigma_prior^2 + q^2/12` (minimal change; q is a
   configuration-known quantity of the Ampcor setup, implementation-
   independent).
2. Floor the effective stop tolerance at q/2 (corollary of 1; the
   crit=0.2 sweep point, with its 160-fold response drop, is the
   empirical support).
3. Guarantee a minimum inlier fraction / warn on degenerate
   retention (the 4.2% elite is a silent structural surprise).
4. Root cure: replace sequential argmax removal with a smooth robust
   loss (IRLS Huber/Tukey) — removes the piecewise-constant jump
   class entirely but changes behavior broadly.
5. Semantics question (the one we most want answered): the w-test
   critical value is conventionally ~3.29 (Baarda); is `crit_value`
   0.1 intended as a standardized statistic threshold, or was
   "0.1 px" the intent?

## 5. Scope and limitations

- Single dataset, single pair; replay determinism claims are
  same-environment claims. "Exact" means exact to the available
  production log precision.
- Pairwise interaction of the 12 flipped nodes was not enumerated
  (the complement probes close the necessity question for the
  observed sets).
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
   matrices) — deferred to the repo owner; the artifacts here carry
   input md5s so the withheld npz stays verifiable.
3. Cross-link from issue #26 once the assets merge.
