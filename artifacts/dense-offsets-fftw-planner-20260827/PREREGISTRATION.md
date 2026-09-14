# bench#36 Step 2 — pre-registration (frozen before results)

Written 2026-08-26, **before** any replicate result was observed. Phase 0
(state regeneration) was running at write time; no replicate, probe, or
comparison output existed yet.

Plan of record: issue #36 final comment (issuecomment-5306812865), as revised
by Karasunoendou after Step 1. Two revisions to that plan are recorded in
§0 below, both forced by source reading done on 2026-08-26.

## 0. Amendments to the recorded plan

### A0-1 — SNAPHU replay cannot use the preserved scratch alone

The recorded plan assumed the preserved `RIFG.h5` was a sufficient seed for
an `unwrap`-step replay. It is not: `phase_unwrap.range_looks/azimuth_looks`
= 13/16 > 1, so `unwrap.run()` re-invokes `crossmul.run(..., dump_on_disk=True)`
inside the step (`nisar/workflows/unwrap.py`). That needs
`fine_resample_slc/`, `geo2rdr/freq{A}/range.off` (flattening), and the
RUNW-side `prepare_insar_hdf5` needs `geo2rdr/freq{A}/{range,azimuth}.off`.
All were deleted by `intermediate_files_removal_enabled: true`.

**Amendment:** run Phase 0 = one full CPU E2E in the run2 environment with
`intermediate_files_removal_enabled: false` (the only runconfig delta), and
replay against that scratch. Replay determinism is a within-Phase-0
property, so it does not matter that Phase 0 is a different realisation
than the 08-16 control/treat runs.

### A0-2 — the OpenMP hypothesis has no code path

The recorded plan's OMP probe presumes OpenMP reductions inside CPU Ampcor.
Source reading contradicts this:

- `dense_offsets.py` selects `isce3.matchtemplate.PyCPUAmpcor` on the CPU path.
- That class is bound in `pybind_isce3/matchtemplate/pycuampcor.cpp`, i.e. it
  is the **pycuampcor CPU translation** (`cxx/isce3/matchtemplate/pycuampcor/*.cpp`).
- That directory contains **no `#pragma omp`, no `std::thread`, no `pthread_`**,
  and sets `nStreams = 1` on the CPU path.
- The sibling `cxx/isce3/matchtemplate/ampcor/` tree — which *does* carry
  `#pragma omp parallel for` — is **absent from `cxx/isce3/Sources.cmake`**
  and is therefore not compiled into `libisce3`.
- All **7** FFTW planner calls in `pycuampcor` use **`FFTW_MEASURE`**; there
  are **zero** `FFTW_ESTIMATE`, **zero** wisdom import/export calls, and no
  `fftwf_init_threads` / `fftwf_plan_with_nthreads`.

`FFTW_MEASURE` selects among candidate algorithms by **wall-clock timing at
plan construction**. Different algorithms decompose the same DFT differently
and therefore round differently. With no wisdom pinning, plan choice can vary
run to run, load to load.

**Amendment:** the OMP arm is demoted to a *falsification check* of the above
static claim (it should show no effect), and the primary mechanistic
hypothesis becomes FFTW_MEASURE plan selection.

## 1. Hypotheses

- **H1 (solver):** SNAPHU is unstable on byte-identical inputs.
- **H2 (unwrap-internal):** the unwrap step's own `crossmul`@13x16 or
  `preprocess` is nondeterministic, so SNAPHU sees different inputs.
- **H3 (FFTW planner):** CPU Ampcor's `FFTW_MEASURE` plans vary run to run,
  changing rounding and producing the observed ULP-level offset noise.
- **H4 (OpenMP):** OpenMP reduction order in CPU Ampcor. **Predicted dead**
  by A0-2; retained only to be falsified.
- **H5 (further upstream):** the noise enters before `dense_offsets`
  (`rdr2geo` / `geo2rdr` / `coarse_resample`).

## 2. Phases, in execution order

### Phase A — unwrap-step replay (>= 3 replicates)

Seeded from Phase 0 scratch by symlink farm; `RIFG.h5` mounted read-only, so
the seed is byte-identical across replicates **by construction**. One
overlay deviation: `snaphu.unwrap(..., delete_scratch=False)`, so the SNAPHU
working directory (solver inputs + config) is retained for hashing. This
does not touch numerics.

Recorded per replicate: sha256 of every retained file under
`scratch/{crossmul,unwrap}`; RUNW datasets (`unwrappedPhase`,
`connectedComponents`, `coherenceMagnitude`, `mask`, the three pixelOffsets
layers, `digitalElevationModel`).

**Decision rule (fixed in advance):**

| Intermediates | RUNW | Conclusion |
|---|---|---|
| identical | identical | unwrap step deterministic → H1, H2 rejected; flips are upstream-driven → go to Phase B |
| identical | differ | **H1 accepted**: SNAPHU itself unstable |
| differ | (either) | **H2 accepted**: crossmul@13x16 / preprocess in scope; localise by which hash differs first |

### Phase B — FFTW planner probe (isolated, no isce3 source change)

A standalone C program builds the *same* plans CPU Ampcor builds
(`n = {160,128}` and `n = {208,144}`, `howmany = 10`, derived from
window 64x96 / half-search 32,32 / SLC oversampling 2 / batch 10x1) with
`FFTW_MEASURE` and no wisdom, and prints an FNV-1a hash of
`fftwf_sprint_plan()`.

Arms: **B-idle** (5 runs, quiescent host) and **B-load** (5 runs, N spin
workers). Run on a quiescent host — never concurrently with another
benchmark, because the planner measures wall-clock.

**Decision rule:** plan hash constant in B-idle *and* B-load → H3 weakened
(planner stable on this host). Plan hash varies in either arm → H3 has a
demonstrated mechanism, and the arm in which it varies says whether
contention is the trigger.

### Phase C — standalone `dense_offsets` replicates

Secondary input is the Phase 0 `coarse_resample_slc`, mounted read-only →
byte-identical across replicates by construction. Compared outputs:
`dense_offsets`, `gross_offsets`, `snr`, `covariance`, `correlation_peak`.

Arms:
- **C-idle**: 3 replicates, `OMP_NUM_THREADS=16` (the harness default), no load.
- **C-load**: 3 replicates, same OMP, N spin workers.
- **C-omp1**: 1 replicate, `OMP_NUM_THREADS=1`, no load.

**Decision rules (fixed in advance):**

1. C-idle replicates differ → `dense_offsets` is nondeterministic with all
   inputs pinned. Combined with A0-2 (single-threaded, FFTW_MEASURE the only
   nondeterministic ingredient) and Phase B, this attributes the noise to
   the planner.
2. C-idle identical but C-load differs → **load-dependent FFTW plan
   selection**; the strongest available result short of a rebuild.
3. All identical → `dense_offsets` deterministic on this host; H3 and H4
   both rejected for this configuration, and **H5** becomes the live
   hypothesis (extend the same replay method to `coarse_resample`,
   `geo2rdr`, `rdr2geo`).
4. C-omp1 vs C-idle: any difference in *output* falsifies A0-2 and revives
   H4. Runtime is also recorded; A0-2 predicts no systematic runtime
   difference either.

## 3. Stopping rule and honesty commitments

- Replicate counts are fixed above. If a phase is inconclusive the count is
  **not** silently raised; any extension is recorded as an amendment with
  its reason, written before the extra runs.
- A null result (Phase C rule 3) is reported as a finding, not retried until
  it breaks.
- Direct confirmation of H3 by switching `FFTW_MEASURE` → `FFTW_ESTIMATE`
  or by pinning wisdom requires editing and rebuilding isce3 source. That is
  **out of scope without explicit sign-off** and is not performed here.
- Every number that reaches an issue comment or report must come from a
  file in this directory. No table cell may be written from memory.
