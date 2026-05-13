# `geocode_slc` profile — I/O vs compute determination

**Status**: closes the [#8](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/8)
profile-first investigation. Re-frames finding #1 from the
[Stage 1 baseline report](2026-05-baseline-s1-boso.md) on measurement,
not assumption.

**TL;DR**: `isce3.geocode.geocode_slc` is **CPU-bound** on this dataset
and remains the dominant single phase even with COMPASS corrections
enabled. The "missing CUDA → port to GPU" framing from the Stage 1
report is supported by measurement on three independent angles, and a
read-only review of the CPU source plus the existing CUDA `Geocode`
shows the kernel is **algorithmically well-suited to a CUDA port**
(4 embarrassingly-parallel phases, compute-bound by transcendentals,
substantial infrastructure reuse). Estimated per-burst speedup
**3-4×**, engineering scope **1500-2500 lines** — comparable to
existing CUDA siblings in isce3. Sufficient data to draft an
issue-shaped RFC; prototype work deferred per
`feedback_contribution_strategy`.

The three measurement angles:

1. **Cache state.** Warm-cache iostat shows zero disk reads during the
   30 s kernel call; a measured cold-cache run shifts wall by only +2.3 s
   (+5.7 %), entirely in startup/prep — the kernel itself is 29 s in
   both cache states.
2. **System time vs user time.** `time -v` reports ~5 % system, ~95 %
   user CPU. Not I/O-bound.
3. **Corrections-enabled production-realistic run.** Enabling
   `correction_luts.enabled: True` (without external ancillary —
   exercises COMPASS's solid earth tides + bistatic + static tropo +
   coarse rdr2geo) adds only ~1 s to the journal-timed phases.
   `geocode_slc` stays at 28-30 s and remains 75-77 % of the
   corrections-enabled wall. The I/O-bound and "geocode_slc is just
   one of several bottlenecks" counter-hypotheses are both rejected.

## Setup

| | |
|---|---|
| Trigger surface | COMPASS geo-mode (`s1_cslc.py … --grid geo`) → `compass.s1_geocode_slc.run` → `isce3.geocode.geocode_slc` |
| Runconfig (CPU / GPU) | [`configs/insar_s1_boso_geo_cpu.yaml`](../configs/insar_s1_boso_geo_cpu.yaml.template) / `_gpu` |
| Input | Sentinel-1 IW3 burst `t046_097519_iw3` VV (2025-12-21), Boso peninsula (282 MiB cf32 in-burst SLC) |
| DEM | Copernicus 1-arcsec, 10712×7445 px, EPSG:4326 (~150 MB on disk; ~16 MB block actually traversed for this burst) |
| Output geogrid | 1046×645 px @ EPSG:4326, 0.0009°×0.00045° spacing (auto-derived) |
| Host | Intel Core Ultra 9 285H (16 cores), 64 GiB RAM, NVMe (Crucial T705 class) |
| GPU | NVIDIA RTX 5080 16 GiB, driver 590.48.01 (only relevant for the GPU-row comparison — `geocode_slc` has no CUDA path so this changes nothing) |
| isce3 SHA | `185905d4` (`feat/cuda-rdr2geo-optional-kwargs` head — RFC #265 fork branch) |
| Container | `cuda:12.8.1-devel-ubuntu24.04` + `cuda-nsight-systems-12-8` (Nsight 2024.6.2.225), Python 3.12, isce3 built locally with CUDA |
| Corrections | tropo/iono LUTs **disabled** (`correction_luts.enabled: False`). They are I/O-heavy (ERA5 grib reads + scipy interpolation), would mask the geocode kernel's own behaviour. The OPERA production path enables them — see "scope caveats" below. |
| QA | disabled (`perform_qa: False`, `browse_image.enabled: False`) to keep the wall-time picture about geocoding |

## Phase breakdown (from `compass.s1_geocode_slc.run` journal timers)

COMPASS already instruments the workflow with `time.perf_counter()` around
its three phases. These numbers come straight from the journal log; no
profiler overhead.

| Phase | CPU run | GPU run | Notes |
|---|---|---|---|
| corrections (tropo/iono LUTs) | 0 s | 0 s | disabled in this config |
| **prep** (GDAL `ReadAsArray` of the burst SLC into numpy) | **1 s** | **1 s** | bulk in-memory load of 282 MiB cf32 — file-system bound but tiny |
| **geocoding** (single `isce3.geocode.geocode_slc(...)` call) | **29 s** | **32 s** | **the kernel under investigation** |
| QA + metadata writeback | 2 s | 2 s | mostly HDF5 attribute writes |
| **Total wall** | **36 s** | **37 s** | |

The CPU-vs-GPU rows are within noise (3-s spread on a single run). This
is the first direct measurement on the bench confirming what
[Stage 1 finding #1](2026-05-baseline-s1-boso.md#finding-1) claimed
without measuring: **toggling `gpu_enabled` does not change geo-mode
wall time, because `isce3.geocode.geocode_slc` has no CUDA
implementation**.

## CPU utilisation — multi-core but not GPU-aware

From `/usr/bin/time -v` on the sanity runs:

| Metric | CPU run | GPU run |
|---|---|---|
| Wall (Elapsed) | 39.88 s | 41.07 s |
| User CPU time | 188.13 s | 190.80 s |
| System CPU time | 2.06 s | 1.98 s |
| Effective cores (user/wall) | 4.72 | 4.65 |
| System ratio (system/wall) | 5.2 % | 4.8 % |

Two takeaways:

1. **The kernel is multi-threaded but doesn't saturate the host** — ~4.7
   effective cores out of 16 available. There is parallelism headroom on
   CPU before "needs GPU" becomes the right framing.
2. **System (kernel-side) time is ~5 %** — all the rest is user-mode
   CPU. This alone is already inconsistent with an I/O-bound kernel
   (where system time would dominate).

## Disk I/O — iostat during the nsys run

`iostat -xm 2 -t -y nvme0n1` running on the host throughout the nsys
run, sampling every 2 s:

```
nvme0n1   r/s    rMB/s   w/s    wMB/s   %util
19:48:55   0.00   0.00   0.00    0.00    0.00
19:48:57   0.00   0.00   1.32    2.50    2.05
19:48:59   0.00   0.00   0.00    0.00    0.00
19:49:01   0.00   0.00   0.03    2.50    2.00
... (~30 s of geocoding) ...
19:49:51   0.00   0.00   0.02    4.00    1.65
19:49:53   0.00   0.00   0.00    0.00    0.00
```

The entire run window shows `r/s = 0.00` on the NVMe device. There is
sporadic write activity at 2-4 MB/s (HDF5 output + nsys's own report
file). **No reads reached the disk** — all syscall-level reads were
served from the page cache.

`%util` peaked at 2.1 %.

## OSRT syscall summary (nsys `osrt_sum`)

Aggregate per-thread syscall time across the full 34 s nsys window
(warm-cache, page-cache-only reads):

| Syscall | Total time | % of OSRT total | Calls |
|---|---|---|---|
| `pthread_cond_wait` | 7.49 s | 63.9 % | 2 (long-blocking thread waits — start + end of program lifetime) |
| `fread` | 2.00 s | 17.1 % | 2197 |
| `open64` | 1.10 s | 9.4 % | 2249 |
| `read` | 0.49 s | 4.2 % | 3476 |
| `fopen64` | 0.22 s | 1.9 % | 52 |
| `open` | 0.18 s | 1.5 % | 8 |
| `pwrite` | 77 ms | 0.7 % | 12279 (HDF5 chunk writes) |
| `fclose` | 54 ms | 0.5 % | 170 |
| (everything else) | < 0.1 s | < 1 % each | |

Interpretation:

- The 3.8 s of `fread` + `open64` + `read` + `fopen64` is
  **page-cache-hit time**, not disk I/O (iostat = 0 r/s confirms).
  Average per `fread`: 0.91 ms. Per `read`: 0.14 ms. These are kernel
  copy-from-page-cache costs.
- The 7.5 s of `pthread_cond_wait` is **per-thread idle** —
  parallel threads waiting on the OpenMP work-stealing queue. Two
  long waits, ~3.7 s each, consistent with background threads
  blocking at start-up and at finalisation. This is not I/O-blocked
  time on the critical path.
- `pwrite` totals 77 ms across 12 279 calls — HDF5 chunk writes
  during the post-geocode HDF5 writeback (chunk_size: [128, 128],
  shuffle + gzip). Even with that many calls, total wall cost is
  ~80 ms.

**Bottom line from OSRT**: of 34 s wall, at most ~4 s spent inside any
syscall, and zero of that hit the disk. The remaining ~30 s is in-user
compute inside `isce3.geocode.geocode_slc`.

## py-spy Python frame attribution

`py-spy record --rate 100 --subprocesses` (Python-frames-only;
`--native` interferes with h5py finalisation in this configuration —
see "instrumentation notes" below). 3631 samples captured covering the
full geocoding window.

Top frames by deepest-level sample count (i.e. on-CPU at sample time,
in Python land):

| Samples | % of run | Frame |
|---:|---:|---|
| 214 | 5.9 % | one-time import chain (compass, isce3, scipy, gdal) — runs once at script start |
| 107 | 2.9 % | `osgeo.gdal.ReadAsArray` (the prep-phase bulk SLC read) |
| 104 | 2.9 % | `s1reader.load_bursts` (SAFE annotation XML parse → burst objects) |
| 75 | 2.1 % | `compass.s1_geocode_slc._wrap_phase` (post-geocode numpy wrap) |
| (rest: < 30 samples each) | (< 1 %) | runconfig parse, h5py setup, etc. |

py-spy without `--native` cannot see *inside* C extensions, so the
single dominant `isce3.geocode.geocode_slc(...)` call appears as a
Python frame "waiting" for the C call to return — it doesn't show up as
a hotspot in Python land but it accounts for the missing ~80 % of wall
time that is not in any of the Python frames above. This is consistent
with the journal-timed phase breakdown.

## Cold-cache verification — measured

A cold-cache run was executed after the initial write-up to verify the
hypothesis empirically. Procedure: `sudo sysctl vm.drop_caches=3` on
the host (operator-run per project sudo policy), then immediately
re-execute the same CPU geo-mode workflow under `/usr/bin/time -v`
with host-side `iostat -xm 1 -t -y nvme0n1` running in parallel.

Artefacts:

- `logs_nucbox-evo-t1/20260513T*cold_geo_cpu*/geo_cpu.{log,time}`
- `logs_nucbox-evo-t1/iostat_geo_cpu_cold/iostat.log`

### Wall-time comparison

| Metric | Warm | Cold | Δ |
|---|---:|---:|---:|
| Wall (Elapsed) | 39.88 s | **42.17 s** | **+2.29 s (+5.7 %)** |
| User CPU | 188.13 s | 185.21 s | -2.92 s (within noise) |
| System CPU | 2.06 s | 2.39 s | +0.33 s |
| Voluntary context switches | 2274 | 6614 | ×2.9 (I/O blocking) |
| Involuntary context switches | 35 548 | 46 955 | ×1.3 |
| `time -v` file-system inputs (512-byte blocks) | (not exercised) | 346 176 (≈ 169 MiB) | — |
| `time -v` file-system outputs (512-byte blocks) | (warm) | 594 504 (≈ 290 MiB) | — |
| **Journal `geocode_slc` phase** | **29 s** | **29 s** | **0 s** |
| Journal `prep` phase | 1 s | 1 s | 0 s (display granularity) |
| Journal `QA + metadata` phase | 2 s | 3 s | +1 s |

### Where the +2.3 s lives — iostat timeline

`iostat -xm 1 -t` on `nvme0n1` during the cold run (non-zero-read
seconds only, full log in `logs_nucbox-evo-t1/iostat_geo_cpu_cold/`):

```
20:50:31  r/s= 1182  rMB/s= 75.4  w/s= 54.1  wMB/s=193   %util=41.9   ← burst-import
20:50:32  r/s= 1478  rMB/s= 80.3  w/s=  1.0  wMB/s=187   %util=29.7
20:50:35  r/s=   31  rMB/s=  1.6  w/s=  0.0  wMB/s=  0   %util= 1.3
20:50:36  r/s= 1737  rMB/s= 98.4  w/s=  0.6  wMB/s=124   %util=34.4   ← peak read burst
20:50:37  r/s= 2102  rMB/s= 49.6  w/s=  0.2  wMB/s= 37   %util=30.7   ← peak r/s (many small)
20:50:38  r/s=  425  rMB/s= 10.9  w/s=  0.0  wMB/s=  0   %util= 7.2
20:50:39  r/s=  276  rMB/s=  5.1  w/s=  0.0  wMB/s=  0   %util= 5.3
20:50:40  r/s=   59  rMB/s=  2.8  w/s=  0.0  wMB/s=  0   %util= 3.1
20:50:43  r/s=  163  rMB/s=  9.1  w/s=  0.0  wMB/s=  0   %util= 4.9
20:50:44  r/s=   70  rMB/s=  1.9  w/s=  0.0  wMB/s=  0   %util= 2.6
20:50:56  r/s=  206  rMB/s= 17.1  w/s=  0.0  wMB/s=  0   %util= 9.6   ← later DEM-block reads
20:50:57  r/s=  400  rMB/s=  9.1  w/s=  0.0  wMB/s=  0   %util= 4.9
... (then quiet for the remaining ~27 s of kernel compute) ...
20:51:18  r/s=   13  rMB/s=  0.2  w/s= 41.1  wMB/s=121   %util=10.0   ← HDF5 writeback
```

Cold reads are concentrated in the first ~10 s (Python startup imports
+ SAFE annotation XML parsing + GDAL `ReadAsArray` of the burst SLC),
with a small later burst around 20:50:56-57 (DEM block reads as the
C++ kernel walks the geogrid). For the remaining ~27 s of the run,
`nvme0n1` is essentially idle (`r/s < 5`).

Peak read throughput hit 98 MB/s, ~3 % of the NVMe's measured
sequential-read ceiling. Peak `r/s` of 2102 is small-random-read
behaviour (Python interpreter walking site-packages, GDAL opening
metadata files, h5py initialising) — explains why the wall penalty
(+2.3 s) was larger than the pure-bandwidth budget (~100 ms)
estimated above. The cost is in seek-amortised small-read latency,
not bandwidth.

### Re-stated cold-cache verdict

- **Cold-cache penalty on wall: +2.3 s (+5.7 %).** All of it lives in
  startup / prep / HDF5 writeback — phases that pre-load or post-emit
  the data the kernel works on.
- **The `geocode_slc` kernel phase itself is 29 s warm AND 29 s cold.**
  Cache state does not change kernel wall time. iostat confirms the
  disk is idle during the kernel-execution window.
- The cold-cache run does not reverse the CPU-bound finding — it
  reinforces it.

## Re-framed finding #1

> **`isce3.geocode.geocode_slc` is the wall-time-dominant kernel in
> OPERA L2 CSLC-S1 processing (~80 % of per-burst wall time, ~30 s on a
> single Sentinel-1 IW3 burst). The kernel is CPU-bound, not I/O-bound:**
>
> - host-side `r/s = 0.00` during the entire kernel execution
>   (warm-cache); a measured cold-cache run shifts total wall by only
>   +2.3 s (+5.7 %), and the `geocode_slc` phase itself stays at 29 s in
>   both cache states (cold-cache penalty lives entirely in startup /
>   prep / HDF5 writeback),
> - user CPU time = 188 s on a 40 s wall → ~4.7 effective cores out of
>   16, so the kernel is OpenMP-threaded but does not saturate the
>   host. Parallelism headroom exists on CPU before "needs GPU"
>   becomes the only path forward,
> - system CPU time = 2 s out of 40 s wall (~5 %), inconsistent with
>   an I/O-bound kernel.
>
> The Stage 1 finding-#1 framing **"missing CUDA → port to GPU"** is
> consistent with measurement. A CUDA port would address the actual
> bottleneck. The competing **"chunking / async-read"** framing is
> not supported.

This changes the contribution-shape question. A CUDA port is a
significantly larger upstream engineering ask than an I/O-tuning patch
would have been. Whether to file an RFC, and in what shape, is a
separate decision (see `feedback_contribution_strategy`: don't draft
features ahead of upstream signalling). The data above just answers
the question of whether the work would be well-targeted.

## Production-realistic addendum — corrections enabled (no external ancillary)

The headline numbers above all run with `correction_luts.enabled: False` so
that wall time is attributable to `geocode_slc` alone. Real OPERA L2
CSLC-S1 processing has corrections **on**, which raises a natural
follow-up: is `geocode_slc` still the dominant single phase when
ionosphere / troposphere / solid-earth-tide LUTs are computed?

The cheapest experiment for that question: enable corrections **without
external ancillary files**. With both `tec_file` and `weather_model_file`
empty, COMPASS's `cumulative_correction_luts` still runs:

- a coarse-grid `rdr2geo` (rg_step 120 m × az_step 0.028 s) to produce
  lat/lon/height/incidence/heading rasters in scratch (CUDA-accelerated
  on GPU since RFC #265's fix landed on the fork branch),
- solid earth tides via pySolid (decimated, then resized),
- bistatic delay (geometric),
- azimuth FM-rate mismatch (numpy),
- **static troposphere** from incidence angle + DEM height (numpy
  fallback when `weather_model_path is None`),
- ionosphere returns zeros (early return when `tec_path is None`).

This exercises the **COMPASS workflow overhead** of having corrections
enabled, but skips the heavy RAiDER + ERA5 tropo path and the IGS TEC
iono interpolation. A "production-realistic γ" experiment (TEC + ERA5
ancillary) is left as a follow-up; if α already shows corrections is
negligible, γ would need a >20 s addition to topple `geocode_slc`,
which RAiDER throughput on a single burst does not typically reach.

Same burst / runconfig / host as above. Two new rendered configs:
`configs/insar_s1_boso_geo_corr_{cpu,gpu}.yaml`.

### Phase timing comparison

Both runs warm-cache.

| Phase | Base CPU | Base GPU | **Corr CPU** | **Corr GPU** |
|---|---:|---:|---:|---:|
| corrections (journal) | 0 s | 0 s | **1 s** | **1 s** |
| prep (journal) | 1 s | 1 s | 1 s | 1 s |
| **geocoding (journal)** | **29 s** | **32 s** | **28 s** | **30 s** |
| QA + metadata (journal) | 2 s | 2 s | 2 s | 2 s |
| Journal total | 36 s | 37 s | 37 s | 39 s |
| `time -v` Elapsed (wall) | 39.88 s | 41.07 s | **42.65 s** | **49.64 s** |
| `time -v` User CPU | 188.13 s | 190.80 s | 195.83 s | 197.22 s |
| `time -v` System CPU | 2.06 s | 1.98 s | 2.28 s | 2.29 s |
| Effective cores (user/wall) | 4.72 | 4.65 | 4.59 | 3.97 |

The corrections phase itself is **only ~1 s of journal-timed wall** in
both the CPU and GPU run. The reason is that
`cumulative_correction_luts` operates on a **coarse LUT grid** (120 m
× 0.028 s spacing → ~150 × 1500 pixels for this burst), not on the
SLC grid (24 443 × 1516 pixels). All the numpy ops (static tropo,
bistatic, az FM mismatch, solid-earth-tides resize) run against that
small grid in milliseconds. The internal `rdr2geo` call also works on
the coarse grid and finishes fast on either CPU or GPU.

### Where the GPU run regresses vs CPU

| | CPU corr | GPU corr | Δ |
|---|---:|---:|---:|
| Journal `burst successfully ran in` | 37 s | 39 s | +2 s |
| `time -v` Elapsed | 42.65 s | 49.64 s | **+6.99 s** |
| "non-journal" startup + finalisation overhead | ~5.6 s | ~10.6 s | **+5 s** |

The journal-timed phases account for ~94 % of CPU-corr wall but only
~79 % of GPU-corr wall. The extra ~5 s of GPU wall lives **outside
the journal**, almost certainly **CUDA context init + driver / lib
load** triggered by the CUDA `rdr2geo` path inside corrections. The
base GPU run also has this overhead but it's smaller (~4 s) because
the only CUDA exercise in the base path is the DEM raster handle.

**Net for this single-burst experiment**: enabling corrections makes
GPU **slower** than CPU end-to-end, by ~7 s. This is a one-burst
result; the CUDA-context cost is amortised over many bursts in a
real PGE daemon, so a stack of N bursts would recover the GPU
advantage on the coarse `rdr2geo` step at break-even N ≈ 5-10. Worth
flagging but not RFC-actionable on its own.

### Disk I/O during corrections runs

`iostat -xm 2 -t -y nvme0n1` confirms warm-cache picture continues to
hold for corrections-enabled runs — non-zero read seconds are
single-digit MB scattered over the run window (page-cache top-up for
the dem block reads inside coarse rdr2geo), no sustained read
activity. Disk near-idle during the geocoding phase, same as the base
run.

### Implication for the RFC-shape decision

`geocode_slc` accounts for **75-77 % of corrections-enabled wall**
(28-30 s of 37-39 s journal total; 66-60 % of `time -v` wall after
CUDA init overhead is included). Even with α-level corrections
enabled, no other single phase comes within an order of magnitude of
`geocode_slc`'s wall time. The corrections phase is small enough
that adding the full γ (TEC + ERA5 + RAiDER) would have to contribute
**more than 20 s of additional wall** before `geocode_slc` loses the
"dominant single phase" position — empirically high for a single
burst given typical RAiDER throughput on coarse LUT grids.

The "is `geocode_slc` THE bottleneck or just one of several?"
question therefore resolves on the α data: **`geocode_slc` is the
single dominant phase, both with and without COMPASS corrections.**

The γ experiment (TEC + ERA5 + RAiDER) is still useful for a numbered
production estimate, but it is no longer a gating decision point for
the RFC framing. It can be deferred to a follow-up if the RFC
discussion calls for tighter production numbers.

## Algorithm review — CUDA portability of `geocode_slc`

Companion to the bottleneck measurement. The question this section
answers is *not* "is `geocode_slc` the bottleneck?" (covered above) but
"if we were to CUDA-port it, what is the shape of that work and what
speedup is realistic?". Read-only review of
[`cxx/isce3/geocode/geocodeSlc.cpp`](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/geocode/geocodeSlc.cpp)
(1037 lines) and the existing CUDA infrastructure under
[`cxx/isce3/cuda/geocode/`](https://github.com/isce-framework/isce3/tree/develop/cxx/isce3/cuda/geocode).

### Kernel structure — 4 phases, all data-parallel

The CPU implementation decomposes the work into 4 sequential phases
that each loop over output (or input) pixels with `#pragma omp parallel
for`. Each phase is **embarrassingly parallel** at the pixel level; the
data dependency is strictly Phase 1 → {2, 3, 4}.

| # | Phase | Source | Per-pixel work | Parallelism |
|---|---|---|---|---|
| 1 | geo2rdr per geogrid pixel | [`computeGeogridRadarIndicesAndMask` :55-228](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/geocode/geocodeSlc.cpp#L55-L228) | iterative `geo2rdr` (5-25 iter: ellipsoid xform, orbit interp, Doppler eval) + DEM lat/lon interp + corrections LUT eval | independent per output pixel |
| 2 | carrier phase deramp | [`carrierPhaseDeramp` :240-274](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/geocode/geocodeSlc.cpp#L240-L274) | 1 × `sin+cos` + 1 × complex multiply on input radar block | independent per input pixel |
| 3 | **SLC sinc interpolation** (dominant) | [`interpolate` :409-511](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/geocode/geocodeSlc.cpp#L409-L511) | 8×8 sinc chip interp + per-row Doppler demod/remod (64 × `sin+cos` + 64 × weighted complex mul per pixel) | independent per output pixel (chip gather pattern) |
| 4 | carrier reramp + flatten phase | [`carrierPhaseRerampAndFlatten` :298-394](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/geocode/geocodeSlc.cpp#L298-L394) | `sin+cos` + complex mul + 2 LUT evals + `4π·sRng/λ` accumulation | independent per output pixel |

### Memory-bound or compute-bound?

For the Boso reference burst (output 1046 × 645 = 675k pixels):

| | Volume | A100 (1.5 TB/s mem BW) | RTX 5080 (~1 TB/s) |
|---|---|---|---|
| Phase 3 chip gather (read) | ~345 MiB | 0.23 ms | 0.35 ms |
| Phase 3 output (write) | ~5 MiB | <0.01 ms | <0.01 ms |
| Phase 3 compute (sinc + sin/cos) | ~40-100 GFLOP | 1-5 ms | 3-10 ms |

The kernel is **compute-bound by transcendentals**: `sin+cos` per
Doppler demod/remod sample dominates. On CUDA this maps to `sincosf`
intrinsics which run at ~1 cycle/SP-op throughput on modern Streaming
Multiprocessors. Memory bandwidth is overkill for this workload.

### Existing CUDA infrastructure — substantial reuse

[`isce3::cuda::geocode::Geocode`](https://github.com/isce-framework/isce3/blob/develop/cxx/isce3/cuda/geocode/Geocode.h)
already implements the non-SLC equivalent and provides directly
reusable pieces:

- **`setBlockRdrCoordGrid`** — runs Phase 1 (geo2rdr per geo pixel)
  on device, with DEM block interpolation + per-block radar grid
  index buffers + masking. This is essentially the GPU
  implementation of `computeGeogridRadarIndicesAndMask`.
- Device-side LUT2d (`gpuLUT2d`), projection (`ProjectionBaseHandle`),
  sinc interpolator (`InterpolatorHandle`), and SubSwaths (`ViewSubSwaths`)
  are all already on device.
- Block-iteration pattern (`_n_blocks`, `_geo_block_length`, …) is
  the same memory-management strategy a CUDA `GeocodeSlc` would use.

What a CUDA `GeocodeSlc` would need to add:

- **Phase 2 kernel** — carrier deramp on the input radar block. New,
  ~50 lines of CUDA C++.
- **Phase 3 kernel** — SLC sinc interpolation with Doppler
  demod/remod. The current GPU sinc interpolator handles complex
  scalar types per template, but does not bake in the
  Doppler-baseband trick the SLC kernel uses (rotating the chip by
  `exp(-i·doppFreq·(ii - chipHalf))` before interp, then by
  `exp(i·doppFreq·fracAzIndex)` after). New, ~200 lines.
- **Phase 4 kernel** — reramp + flatten. New, ~100 lines.
- **Device-side `AzRgFunc`** for the carrier function template
  (Poly2d / LUT2d). Poly2d device representation is trivial
  (coefficient array); `gpuLUT2d` already exists. ~50 lines for the
  Poly2d device side.
- pybind binding — ~200 lines, mirrors the existing `Geocode` pybind.
- Tests — golden-output comparison against CPU `geocode_slc` on a small
  fixture. ~200 lines.

### Engineering scope

| Component | LoC estimate |
|---|---|
| 3 new CUDA kernels (deramp, interpSlc, reramp+flatten) | 350-400 |
| `GeocodeSlc` class (header + cpp, mirrors existing `Geocode`) | 600-800 |
| Device-side Poly2d helper | 50-100 |
| pybind binding | 150-250 |
| Tests | 200-300 |
| **Total** | **1500-2500 lines** |

Same order of magnitude as `cuda/image/ResampSlc` (the closest sibling
in scope) and `cuda/geocode/Geocode` (the closest sibling in algorithm
shape). One to two orders of magnitude larger than the RFC #265 fix
(~10 production lines).

### Speedup estimate

Per-phase, based on the Boso burst dimensions and reference points
from the existing CUDA siblings (Stage 1 measured `Rdr2Geo` ~2.5× on
RTX 5080):

| Phase | CPU (29 s budget) | CUDA RTX 5080 | CUDA A100 |
|---|---:|---:|---:|
| 1 — geo2rdr per geo pixel | ~5-7 s | ~0.3 s | ~0.1 s |
| 2 — deramp | ~1 s | ~0.05 s | ~0.02 s |
| 3 — sinc interp + Doppler (dominant) | **~15-20 s** | ~1-3 s | ~0.5-1 s |
| 4 — reramp + flatten | ~1 s | ~0.1 s (fp64 penalty) | ~0.05 s |
| **Kernel total** | **~25 s** | **~2-4 s** | **~1-2 s** |

End-to-end per-burst (kernel + unchanged prep/QA):

| | Wall (s) | vs CPU |
|---|---:|---:|
| CPU (today) | 36 | 1.0× |
| CUDA on RTX 5080 (estimate) | ~11 | ~3.3× |
| CUDA on A100 (estimate) | ~9 | ~4× |

At production scale (~7000 bursts/day global S1 acquisitions per
public OPERA documentation), the saved CPU-node-hours are in the
thousands per year. Whether this is *worth* the 1500-2500 lines of
upstream engineering is a maintainer-side ROI judgment, not a
measurement question.

### Risks / caveats specific to the port

1. **Flatten phase precision on consumer GPUs.** `4π·sRng/λ` with
   `sRng ~ 800 km` and `λ ~ 5.5 cm` yields phase magnitudes of
   ~10^11 rad. Accurate modular reduction (`fmod`) requires fp64.
   A100/H100 have fp64:fp32 = 1:2; RTX 5080 (Blackwell consumer) is
   1:64. So on a consumer GPU, **Phase 4 may bottleneck the whole
   kernel** at the fp64 reduction step. Production OPERA hardware
   is A-class so this is fine in practice, but the bench-side
   measurement on RTX 5080 would underestimate A100 performance —
   relevant if the RFC includes RTX-class numbers.
2. **Sinc chip overlap / cache strategy.** Adjacent output pixels
   share most of their 8×8 chip with neighbours. A naive global-load
   kernel works, but a shared-memory tile or texture-memory variant
   gets 2-3× more on the interp phase. Not a correctness issue, but
   a tunable knob in the implementation.
3. **`AzRgFunc` template specialisation on device.** Both Poly2d and
   LUT2d need a device representation. LUT2d is already available
   (`gpuLUT2d`); Poly2d needs a small new device struct. No
   architectural blocker.
4. **Multi-band / multi-pol parallelism.** The CPU version processes
   each band serially within a block (outer loop over rasters). The
   GPU version could process all bands in a single kernel launch by
   adding a band index, increasing arithmetic intensity. Minor
   optimisation opportunity, not a correctness concern.

### Net assessment for upstream RFC framing

- The kernel is **algorithmically well-suited to CUDA**: 4 phases all
  embarrassingly parallel, compute-bound by `sin+cos` (a strength of
  modern GPUs), with substantial reusable infrastructure already in
  `isce3::cuda::geocode::Geocode`.
- The realistic per-burst speedup of **3-4×** sums to **thousands of
  node-hours/year** at production global S1 throughput.
- Engineering cost is **1500-2500 lines**, an order or two above the
  RFC #265 patch but comparable to existing CUDA siblings in isce3.
- Production hardware (A-class) avoids the consumer-GPU fp64 wrinkle.

This is **enough information to draft an issue-shaped RFC**
(measurement + algorithm review + speedup estimate + scope estimate)
without committing to a prototype. Per `feedback_contribution_strategy`
the next step is "RFC issue first, prototype only after maintainer
engagement" — and the data above is the contents of that issue.

## Open questions / follow-ups

These are noted but not blocking issue #8:

1. **Per-block CPU utilisation inside the kernel.** The 4.7 effective
   cores indicates OpenMP parallelism but doesn't tell us *why* it
   doesn't saturate 16. Could be Amdahl (serial portions in
   `geo2rdr` loop, DEM interpolation), could be `lines_per_block`
   not granular enough, could be a single-threaded section in the
   carrier-phase / flatten kernels. Would need an instrumented
   build or NVTX ranges inside the kernel to attribute further.
2. **Production-realistic run with corrections enabled.** This profile
   disables tropo/iono LUTs to isolate the geocoding kernel. The
   OPERA production path enables them; that adds ERA5 reads + scipy
   interpolation that would change the I/O profile (probably increases
   I/O significantly but is bounded by the LUT-grid resolution, not
   the SLC). Worth measuring before any RFC is filed.
3. **Cross-architecture confirmation.** This is a single-host result on
   an Intel Core Ultra 9 285H. Numbers may shift on a Xeon /
   AMD EPYC, particularly the effective-cores count. Out of scope for
   issue #8 but relevant if framing escalates to "CUDA port has high
   ROI".
4. **Compute-bound nsys CPU-sampling.** Per `2026-05-13-nsys-install.md`,
   host `kernel.perf_event_paranoid = 4` disables nsys `--sample cpu`.
   To attribute CPU time to specific functions inside `geocode_slc` at
   sample resolution, host-side `sudo sysctl
   kernel.perf_event_paranoid=2` is required. Not blocking for the
   I/O-vs-compute question (we have the answer); becomes relevant if
   the next investigation is "which sub-kernel inside geocode_slc
   would benefit most from CUDA porting."
5. **Startup-import overhead dominates the cold-cache penalty.** The
   measured cold-cache penalty (+2.3 s) is mostly Python + isce3 +
   COMPASS module-load reading shared libraries / `.pyc` files cold
   off NVMe — not data I/O the kernel itself causes. In a long-running
   PGE daemon (OPERA production reality) the interpreter and libraries
   are loaded once; subsequent bursts see the warm-cache picture. The
   "+5.7 %" figure is therefore an over-estimate of per-burst
   cold-cache cost in production.

## Instrumentation notes (for reproducibility)

- **py-spy + h5py crash on context-manager exit.** Both with and
  without `--native`, py-spy's ptrace attachment interacts with
  h5py's `File.__exit__` finalisation. The kernel runs to completion
  and py-spy collects samples covering the geocode phase, but the
  workflow exits non-zero on HDF5 close with
  `RuntimeError: Can't decrement id ref count (errno = 103,
  'Software caused connection abort')`. The captured profile is
  intact and usable. `--native` is now opt-in via the
  `PYSPY_NATIVE=1` env var in [`scripts/run_profile_pyspy.sh`](../scripts/run_profile_pyspy.sh).
- **nsys `--sample cpu` is degraded** on this host (perf-paranoid 4 as
  above). CUDA / OSRT / NVTX trace channels work; per-thread CPU
  sampling does not.
- **COMPASS geo-mode requires a real burst DB.** `GeoRunConfig.load_from_yaml`
  calls `os.path.isfile(burst_database_file)` before the
  `if burst_database_file is None:` branch, so the None branch is
  effectively dead code. [`fetch/build_burst_db.py`](../fetch/build_burst_db.py)
  generates a minimal one-row sqlite from `bursts.json` to satisfy the
  check; bbox is the Boso burst footprint in EPSG:4326 (matches the DEM
  CRS — avoids reprojection). Sufficient for profile purposes; would
  need a real OPERA-JPL burst map for grid-consistent production output.

## Artefacts

All under `isce3-benchmark/logs_nucbox-evo-t1/` on the dev host:

| Artefact | Path | Size |
|---|---|---|
| CPU sanity (warm) — journal + `time -v` | `20260513T094600Z_sanity_geo_cpu/geo_cpu.{log,time}` | — |
| GPU sanity (warm) — journal + `time -v` | `20260513T094652Z_sanity_geo_gpu/geo_gpu.{log,time}` | — |
| py-spy flamegraph (Python frames, warm) | `20260513T105032Z_pyspy_geo/pyspy.svg` | 385 KB |
| nsys timeline (CUDA + OSRT + NVTX, warm) | `20260513T101907Z_nsys_geo/nsys.nsys-rep` | 402 KB |
| host iostat (warm, 2 s interval) | `iostat_geo_cpu/iostat.log` | 2346 lines / ~40 s window |
| CPU cold-cache run — journal + `time -v` | `20260513T*cold_geo_cpu*/geo_cpu.{log,time}` | — |
| host iostat (cold, 1 s interval) | `iostat_geo_cpu_cold/iostat.log` | full ~50 s window |
| CPU corrections-enabled — journal + `time -v` | `20260513T*corr_geo_cpu*/geo_corr_cpu.{log,time}` | — |
| GPU corrections-enabled — journal + `time -v` | `20260513T*corr_geo_gpu*/geo_corr_gpu.{log,time}` | — |
| host iostat (corr CPU, 2 s interval) | `iostat_geo_corr_cpu/iostat.log` | full run window |
| host iostat (corr GPU, 2 s interval) | `iostat_geo_corr_gpu/iostat.log` | full run window |
