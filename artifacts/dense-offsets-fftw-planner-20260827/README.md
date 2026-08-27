# dense_offsets is not run-to-run reproducible — FFTW_MEASURE plan selection

Evidence bundle for [bench#36](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/36) Step 2, 2026-08-26/27.

## One-line result

With **both Ampcor inputs byte-identical**, a standalone `dense_offsets` run
produced **9 distinct results in 9 runs**. The only nondeterministic
ingredient in that step is FFTW's `FFTW_MEASURE` planner, which is used
without wisdom and re-measures candidate algorithms in every process.

## What was asked and what came back

Step 2 was planned as "SNAPHU replay → OpenMP probe". Both legs turned over:

| Pre-registered hypothesis | Outcome |
|---|---|
| H1 SNAPHU itself is unstable | **rejected** (Phase A) |
| H2 unwrap-internal `crossmul`@13x16 / `preprocess` | **rejected** (Phase A) |
| **H3 `FFTW_MEASURE` plan selection** | **accepted** (Phase B + C) |
| H4 OpenMP reduction order in CPU Ampcor | **no code path** (static); arm effect **not decidable at n=3** |
| H5 nondeterminism enters further upstream | not needed |

## Phases

**Phase 0 — state regeneration.** One full CPU E2E (5585.0 s, rc=0) with
`intermediate_files_removal_enabled: false` as the only runconfig delta, so
the deleted intermediates the replays need (`fine_resample_slc`, `geo2rdr`
offsets, `coarse_resample_slc`) survive. See `PREREGISTRATION.md` §A0-1 for
why the recorded plan's "replay from the preserved RIFG" was not sufficient.

**Phase A — unwrap-step replay, 3 replicates** (`RESULTS_PHASE_A.md`).
Seeded from Phase 0 by a symlink farm; `RIFG.h5` mounted read-only. The
single overlay deviation is `snaphu.unwrap(..., delete_scratch=False)`, so
the exact bytes SNAPHU consumed and produced are retained and hashed
separately. **18/18 intermediates identical, all 8 RUNW datasets identical,
all statistics attributes identical.** The unwrap step is bit-for-bit
deterministic on fixed inputs, so the E2E ±2π flips are not made there.

**Phase B — isolated FFTW planner probe** (`RESULTS_PHASE_B.md`).
`fftw_probe/fftw_plan_probe.c` builds the same plans CPU Ampcor builds
(`n = {160,128}` and `n = {208,144}`, `howmany = 10`, derived from
window 64x96 / half-search 32,32 / SLC oversampling 2 / batch 10x1) with
`FFTW_MEASURE` and no wisdom, then transforms a **fixed** deterministic
input and hashes both the plan and the output. On a quiescent host
(gate: 3 consecutive 5 s samples < 8% CPU), 5 runs gave up to **4 distinct
plans and 4 distinct outputs** for a byte-identical input
(`distinct inputs = 1`, measured). Median relative difference
**1.12e-07 - 1.25e-07**, i.e. float32 machine epsilon (1.192e-07).
No isce3 source change was needed to show this.

**Phase C — standalone `dense_offsets`, 3 arms x 3 replicates**
(`RESULTS_PHASE_C.md`). Secondary input is the shared Phase 0
`coarse_resample_slc` (read-only mount, identical by construction);
`reference.slc` measured identical across replicates.

| output | distinct values / 9 runs |
|---|---|
| `gross_offsets` | 1 |
| `snr` | 4 |
| `covariance` | 4 |
| **`dense_offsets`** | **9** |
| **`correlation_peak`** | **9** |

`correlation_peak` differs at max **7.15e-07** — the same value the 08-16
E2E bundle recorded for `correlationSurfacePeak`
(`artifacts/cpu-e2e-nondeterminism-20260816/quantify_diffs_output.txt`:
`max|d|=7.15256e-07`). `dense_offsets` differences are quantised: when the
raw-correlation stage lands on the dominant result the spread is bounded by
**3.125e-02 = 1/32 px** — the oversampling quantum that
[isce3#351](https://github.com/isce-framework/isce3/issues/351) showed can
discontinuously change polyfit inlier membership. When the raw stage lands
elsewhere the spread reaches **~60 px**, the search-window scale.

## What is NOT claimed

- **No arm effect is established.** `snr`/`covariance` fall into a dominant
  equivalence class shared by 6 of 9 runs (idle 1,2,3 / load 1 / omp1 2,3)
  plus three singletons (load 2, load 3, omp1 1). Arm counts of 0/3, 2/3 and
  1/3 excursions cannot separate chance from an arm effect at n=3, so
  "load destabilises the raw stage" is **not** supported by this data, and
  `AMENDMENT_A1.md`'s replacement rule for H4 is likewise inapplicable — it
  assumed the signature was a property of the arm rather than a draw from a
  shared distribution.
- **The omp1 arm does not isolate OpenMP.** `harness/run_dof.sh` sets
  `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and `OPENBLAS_NUM_THREADS` together.
- **The runtime difference is real but unexplained.** idle 519/564/551 s,
  omp1 701/703/614 s (non-overlapping ranges), load 1139/1141/1199 s.
  `PREREGISTRATION.md` §A0-2 predicted no runtime difference for the omp1
  arm; that prediction **failed**. `nproc` reporting 1 under
  `OMP_NUM_THREADS=1` is a coreutils behaviour, not CPU restriction
  (`nproc --all = 16`, affinity 16, measured). `copy_raster` was read and is
  a plain serial loop. The mechanism is not established and is not guessed at
  here.
- **Cross-machine bitwise reproducibility is out of scope.** The claim is
  scoped to run-to-run on one host and build.
- **`focus.py` / `RangeComp` is unmeasured.** `isce3::fft` carries the same
  planner default (plus `threads = omp_get_max_threads()`), and `RangeComp`
  is reachable from `nisar.workflows.focus`, but no measurement of that path
  was made. See `STATIC_ANALYSIS.md`.

## Method notes worth reusing

- snaphu-py names its scratch files with `mkstemp` (random token, mode 0600,
  container root). `harness/compare_reps.py` canonicalises the names and
  normalises the paths embedded in `snaphu.config.*.txt`; comparisons run
  **inside** the container (`harness/run_compare_*.sh`) because a host-side
  `sha256sum` gets `Permission denied` on some of them — and, misleadingly,
  succeeds on others.
- A host-side `rm -rf` cannot clean container-created directories;
  `harness/clean_rep.sh` does it from inside a container.
- Two defects block the genuine standalone `unwrap` entrypoint; they are
  filed separately as
  [bench#43](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/43)
  and worked around in `harness/replay_unwrap.py`.

## Provenance

- Host: NucBox EVO T1, 16 CPUs, CPU-only workload.
- isce3: v0.25.16 from-source container build (`isce3-benchmark:dev`);
  static analysis cross-checked against upstream `develop` `2919e1c97`.
- Dataset: NISAR L-SAR sample pair, ascending track 139 frame 019,
  2026-07-05 / 2026-07-17 (same pair as the 08-16 CPU E2E bundle).
- `SHA256SUMS` covers every file in this directory.
