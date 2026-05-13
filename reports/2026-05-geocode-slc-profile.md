# `geocode_slc` profile — I/O vs compute determination

**Status**: closes the [#8](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/8)
profile-first investigation. Re-frames finding #1 from the
[Stage 1 baseline report](2026-05-baseline-s1-boso.md) on measurement,
not assumption.

**TL;DR**: `isce3.geocode.geocode_slc` is **CPU-bound** on this dataset.
The "missing CUDA → port to GPU" framing from the Stage 1 report is
supported by measurement. The I/O-bound counter-hypothesis (which would
have implied a smaller-scoped chunking / async-read RFC) is **not
supported**: warm-cache iostat shows zero disk reads during the 30 s
kernel call, and a measured cold-cache run shifts wall by only +2.3 s
(+5.7 %) — entirely in startup/prep, not in the kernel itself, which
remains at 29 s in both cache states.

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
