# CPU InSAR is not run-to-run reproducible: FFTW_MEASURE plan selection in Ampcor

- Date: 2026-08-26/27
- Host: NucBox EVO T1, 16 CPUs, CPU-only workload. Quiescence gate for the
  planner probe: 3 consecutive 5 s samples below 8% system CPU.
- isce3 commit: v0.25.16, from-source container build (`isce3-benchmark:dev`).
  All static claims cross-checked against upstream `develop` `2919e1c97`
  and hold there unchanged.
- isce3-benchmark commit: this branch (`feat/bench36-step2-determinism`)
- Runconfig: `artifacts/dense-offsets-fftw-planner-20260827/configs/` —
  `insar_phase0.yaml` (E2E, `intermediate_files_removal_enabled: false`),
  `unwrap_rep.yaml`, `dof_rep.yaml`
- Dataset: NISAR L-SAR sample pair, ascending track 139 frame 019,
  2026-07-05 / 2026-07-17 — the same pair as
  `artifacts/cpu-e2e-nondeterminism-20260816/`
- Pre-registration: `artifacts/dense-offsets-fftw-planner-20260827/PREREGISTRATION.md`
  (sha256 `c8883a3df81b77cc7291280731d9b99f9acd117b63235326c3ec688539097ad0`,
  frozen before any result was observed), plus `AMENDMENT_A1.md`

## Summary

The 08-16 CPU E2E A/B found that two runs of the same pipeline on the same
inputs differ, with `unwrappedPhase` disagreeing by ±2π on a large fraction
of pixels. That report attributed the noise to "CPU Ampcor run-to-run ULP
noise" without identifying a mechanism. This study identifies it.

With **both Ampcor inputs byte-identical**, a standalone `dense_offsets`
run produced **9 distinct results in 9 runs**. The step contains no OpenMP
and no threading; its only nondeterministic ingredient is FFTW's
`FFTW_MEASURE` planner, used without wisdom. An isolated probe reproducing
the same transforms shows that planner selecting different algorithms on an
idle host and producing different output bits for identical input, at
float32 machine epsilon.

The unwrap step — SNAPHU included — is bit-for-bit deterministic. It
propagates the noise; it does not create it.

## Two ingredients, audited across the whole chain

A deterministic algorithm becomes non-reproducible through (1)
order-dependent floating-point accumulation across threads, or (2)
timing-benchmarked FFT planning. Every module in `cxx/isce3/Sources.cmake`
was audited for both (`STATIC_ANALYSIS.md`).

| Step | implementation | FFT planner | order-dependent FP accumulation |
|---|---|---|---|
| `rdr2geo` | `geometry/Topo.cpp` | none | no — the only `omp atomic` is an int counter |
| `geo2rdr` | `geometry/Geo2rdr.cpp` | none | no — `reduction(+:converged)`, an int counter |
| resample | `image/Resample.cpp` | none | no — disjoint output pixels, no reduction |
| **`dense_offsets`** | **`matchtemplate/pycuampcor/*.cpp` via `PyCPUAmpcor`** | **`FFTW_MEASURE`, no wisdom** | n/a — **no OpenMP or threading at all**, `nStreams = 1` |
| `crossmul` | `signal/Crossmul.cpp` → `signal/Signal.cpp` | **`FFTW_ESTIMATE`** | no |
| `unwrap` | SNAPHU via `snaphu-py` | none | `nproc: 1`, `ntiles: [1,1]` |
| statistics | `math/Stats.cpp` | — | order-dependent merge exists, but not on the NISAR path (below) |

**CPU Ampcor is the only `FFTW_MEASURE` exposure in this chain**, and it is
exactly the step whose outputs the 08-16 bundle found differing.

Two details make this an inconsistency rather than an oversight. First,
`signal/Signal.cpp` writes `FFTW_ESTIMATE` explicitly at all four of its
plan sites — someone chose determinism there. Second, the seven
`FFTW_MEASURE` sites are all in `matchtemplate/pycuampcor/`, which is a
mechanical CPU translation of the CUDA Ampcor kernels; the flag reads as an
artifact of porting cuFFT calls to FFTW rather than a decision about the
InSAR pipeline. Nothing in the codebase imports or exports FFTW wisdom, so
every `FFTW_MEASURE` plan is re-measured in every process.

## Phase A — the unwrap step is deterministic

Three replicates of the unwrap step, seeded from a retained Phase 0 scratch
with `RIFG.h5` mounted read-only. `snaphu.unwrap(..., delete_scratch=False)`
retains the exact bytes SNAPHU consumed and produced, so solver inputs and
solver outputs are hashed separately.

- 18/18 retained intermediates identical — including `wrapped_igram.filt`
  (preprocess), `wrapped_igram_rg13_az16` / `coherence_rg13_az16`
  (crossmul@13x16), `snaphu.igram` / `snaphu.corr` (solver in),
  `snaphu.unw` / `snaphu.conncomp` (solver out), and the regenerated
  `RUNW_{offsets,ifgram}_dem.rdr` (CPU Topo).
- All 8 compared RUNW datasets identical.
- All four statistics attributes identical on every compared dataset
  (their presence was verified, so this is a real null, not a skipped loop).

H1 and H2 are rejected. The ±2π flips enter upstream of unwrap.

## Phase B — the planner is unstable on an idle host

`fftw_plan_probe.c` builds the plans CPU Ampcor builds — `n = {160,128}`
and `n = {208,144}`, `howmany = 10`, derived from window 64x96,
half-search 32/32, SLC oversampling 2, batch 10x1 — with `FFTW_MEASURE`
and no wisdom, then transforms a fixed deterministic input. Planning
happens before the input is installed, since `FFTW_MEASURE` overwrites the
arrays while measuring.

Five runs, quiescent host, `distinct inputs = 1` (measured, not assumed):

| transform | distinct plans | distinct outputs |
|---|---|---|
| `raw_r2c` (160x128, x10) | 4 / 5 | **4 / 5** |
| `raw_c2r` | 2 / 5 | **4 / 5** |
| `oversampled_r2c` (208x144, x10) | 3 / 5 | **2 / 5** |
| `oversampled_c2r` | 4 / 5 | **4 / 5** |

Hashing the output as well as the plan matters: plan differences could have
been numerically inert. They are not. Median relative difference between
differing runs is **1.25e-07** (`raw_r2c`) and **1.12e-07**
(`oversampled_r2c`) — float32 machine epsilon is 1.192e-07.

## Phase C — `dense_offsets` inherits it

Three arms of three replicates. `reference.slc` measured identical across
replicates; the secondary comes from a shared read-only mount.

| output | distinct values / 9 runs |
|---|---|
| `gross_offsets` | 1 |
| `snr` | 4 |
| `covariance` | 4 |
| **`dense_offsets`** | **9** |
| **`correlation_peak`** | **9** |

`correlation_peak` differs at max **7.15e-07**, matching the 08-16 E2E
bundle's `correlationSurfacePeak` max|d| of **7.15256e-07** — the isolated
single-step measurement and the 6178 s pipeline land on the same number.

`dense_offsets` differences come in two scales. `snr` and `covariance` are
raw-correlation-stage products and fall into a dominant equivalence class
shared by 6 of the 9 runs, plus three singletons. Runs inside the dominant
class differ only through the oversampled stage, and their `dense_offsets`
spread is bounded by **3.125e-02 = exactly 1/32 px**. Runs that land
outside it differ in integer argmax decisions, and the spread reaches
**~60 px** — the search-window scale.

That 1/32 px quantum is the same one
[isce3#351](https://github.com/isce-framework/isce3/issues/351) showed can
discontinuously flip rubbersheet polyfit inlier membership. #351 documented
the amplifier using a GPU-vs-CPU difference; this study shows the same
quantum being crossed **run to run on one machine with identical inputs**.

## What this study does not establish

- **No arm effect.** Excursions from the dominant `snr`/`covariance` class
  occurred 0/3 (idle), 2/3 (load), 1/3 (omp1). At n=3 per arm this cannot
  separate chance from an arm effect, so no causal claim is made about
  system load. `AMENDMENT_A1.md`'s replacement rule for H4 is inapplicable
  for the same reason: it assumed the signature was a property of the arm.
- **H4 is undecided, not rejected.** The source-level facts (no OpenMP or
  threading in `pycuampcor`; the OpenMP-carrying `matchtemplate/ampcor/`
  tree is absent from `Sources.cmake`) stand on their own. But the
  pre-registered runtime prediction **failed**: idle 519/564/551 s vs omp1
  701/703/614 s, non-overlapping. `OMP_NUM_THREADS` affects this step's
  runtime by a mechanism not identified here — `nproc` reporting 1 is
  coreutils honouring the variable, not CPU restriction (`nproc --all = 16`,
  affinity 16, measured), and `copy_raster` is a plain serial loop.
  The omp1 arm also sets `MKL_NUM_THREADS` and `OPENBLAS_NUM_THREADS`
  together, so it does not isolate OpenMP.
- **`Stats.cpp` is not implicated for NISAR products.** Its
  `#pragma omp critical` merge is a genuinely non-associative Chan-style
  update, but the path NISAR uses (`computeRasterStats`) writes per-block
  results into fixed slots and aggregates them serially in index order via
  `_aggregateStats`. The hazard is reachable only from the directly exposed
  pybind API with the default `parallel=True`.
- **Cross-machine bitwise reproducibility is out of scope.** FFTW selects
  SIMD codelets by CPU features and GPU and CPU differ by construction. The
  defensible target is: the same machine and build, run twice, gives the
  same answer.
- **`focus.py` is unmeasured.** `isce3::fft` defaults to `FFTW_MEASURE`
  *and* to `threads = omp_get_max_threads()`, and `RangeComp` reaches it
  from `nisar.workflows.focus` (L0B→RSLC range compression). Same hazard,
  second location, no measurement. Deferred to a dedicated study.

## Remediation options (all in software)

1. **`FFTW_ESTIMATE`** in `pycuampcor`, matching what `Signal.cpp` already
   does. Deterministic for a given build, dimensions and alignment. Costs
   transform speed.
2. **`FFTW_MEASURE` plus pinned wisdom** — measure once, export, import
   thereafter; `FFTW_WISDOM_ONLY` prevents silently falling back to
   measuring. Keeps `MEASURE`-quality plans and is reproducible. Needs an
   operational decision about where wisdom lives and when it is regenerated.
3. **Pin thread counts** where `isce3::fft` is used, since
   `threads = omp_get_max_threads()` is a second, independent reproducibility
   axis from planner timing.

Which trade-off is acceptable is an upstream decision; this report's
contribution is the measurement, not the choice.

## Side-findings

Two defects block the genuine standalone `unwrap` entrypoint on both
v0.25.16 and `develop`, filed as
[bench#43](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/43):
`RUNW_STANDALONE` is missing from `h5_prep.get_products_and_paths()`'s
`product_dict` (KeyError), and `crossmul.run()` opens
`<scratch>/crossmul/product.h5` before the loop body that creates the
directory (FileNotFoundError on a fresh scratch). Both are worked around in
the harness; neither required an isce3 source change.
