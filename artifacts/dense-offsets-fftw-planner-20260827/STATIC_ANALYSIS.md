# bench#36 Step 2 — static analysis addendum

Written 2026-08-26 while Phase 0 was running. **Still no replicate, probe, or
comparison result had been observed.** `PREREGISTRATION.md` is unmodified
(sha256 `c8883a3df81b77cc7291280731d9b99f9acd117b63235326c3ec688539097ad0`);
this file records source-reading that extends §A0-2 of it. All claims here
are from reading `isce3-v0.25.16` and are independently checkable; none rest
on measurement.

## Two ingredients can make a deterministic algorithm non-reproducible

1. **Order-dependent floating-point accumulation** across threads (OpenMP
   reductions, atomics or critical sections that merge partial float results).
   A plain `#pragma omp parallel for` writing disjoint outputs is *not* such a
   case.
2. **Timing-benchmarked FFT planning** (`FFTW_MEASURE` / `FFTW_PATIENT`
   without pinned wisdom): FFTW times candidate algorithms at plan
   construction and keeps the fastest. Different algorithms decompose the
   same DFT differently, so they round differently.

Both were audited across every module in `cxx/isce3/Sources.cmake`.

## Result for the CPU InSAR L1 chain used by this runconfig

| Step | implementation | FFT planner | order-dependent FP accumulation? |
|---|---|---|---|
| `rdr2geo` | `geometry/Topo.cpp` | none | no — the only `omp atomic` is `totalconv += totalconv_thread` (an int counter, line 249) |
| `geo2rdr` | `geometry/Geo2rdr.cpp` | none | no — `reduction(+:converged)`, an int counter (line 139) |
| `coarse`/`fine resample` | `image/Resample.cpp` | none | no — `omp for collapse(2)` over disjoint output pixels, no reduction |
| **`dense_offsets`** | **`matchtemplate/pycuampcor/*.cpp` via `PyCPUAmpcor`** | **`FFTW_MEASURE`, no wisdom** | n/a — **no OpenMP or threading at all**; `nStreams = 1` |
| `crossmul` | `signal/Crossmul.cpp` → `signal/Signal.cpp` | **`FFTW_ESTIMATE`** (all 4 plan sites) | no — `reduction(+:n)` is a thread-count helper (line 63); the rest are disjoint-write `parallel for` |
| `unwrap` | SNAPHU via `snaphu-py` | none | `nproc: 1`, `ntiles: [1,1]` |
| statistics | `math/Stats.cpp` | — | **yes** — per-thread partials merged under `#pragma omp critical` (line 161) |

### Consequences

- **CPU Ampcor is the only `FFTW_MEASURE` exposure in this chain.** That is
  exactly the step whose outputs the 08-16 evidence bundle found differing
  (`RIFG` `alongTrackOffset` / `slantRangeOffset` / `correlationSurfacePeak`
  — the Ampcor products — at float32 ULP).
- **`crossmul` is not exposed**, because `Signal.cpp` plans with
  `FFTW_ESTIMATE`. This matters for reading Phase A: if the unwrap step's
  internal `crossmul`@13x16 turns out to differ across replicates, the cause
  is *not* planner variability and the static picture needs revisiting.
- **No step in this chain has order-dependent FP accumulation affecting
  pixel data.** `Stats.cpp` does, but only for the `mean_value` /
  `sample_stddev` / `min_value` / `max_value` **attributes**. Phase A
  compares attributes separately from arrays for this reason.

### Latent exposure outside this chain (not measured here)

`isce3::fft` (`fft/detail/FFTPlanBase.h`, `fft/FFT.icc`) defaults to
`FFTW_MEASURE` *and* to `threads = omp_get_max_threads()`
(`fft/detail/Threads.cpp`). Its consumers are `focus/RangeComp.cpp` and
`signal/CrossMultiply.cpp` — neither is reached by the NISAR InSAR CPU
workflow, so it is out of scope for bench#36. Recorded because it is the
same hazard in a second place, relevant to any future focusing benchmark.
The convenience overloads `fft()` / `ifft()` in `FFT.icc` use
`FFTW_ESTIMATE` and are unaffected.

### Nothing imports or exports wisdom

`signal/fftw3cxx.h` declares `import_wisdom_*` / `export_wisdom_*` wrappers,
but no call site in `cxx/` invokes them, and there is no
`fftw_import_system_wisdom()` call. So every `FFTW_MEASURE` plan in the
codebase is re-measured from scratch in every process.

## What this does and does not establish

It establishes that **if** dense_offsets is nondeterministic on byte-identical
inputs, the planner is the only candidate mechanism left inside that step, and
that H4 (OpenMP) has no code path anywhere in the chain that could affect
pixel data.

It does **not** establish that the planner actually varies on this host. That
is what Phase B (isolated plan-hash probe) and Phase C (replicate
`dense_offsets` runs) are for, and either may return a null result — see the
Phase C rule-3 branch in the pre-registration.
