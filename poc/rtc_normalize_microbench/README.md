# RTC `_normalizeRtcArea` microbenchmark

Isolates the gamma-naught normalization loop from
`cxx/isce3/geometry/RTC.cpp` and compares four implementations on
identical inputs at NISAR freq-A scale (29240 x 21232 `float`). Motivated
by [isce3#341](https://github.com/isce-framework/isce3/issues/341): the
workflow-level A/B in the issue can no longer resolve differences between
*fixed* variants (the fixed loop is <1% of workflow wall, below the
±1.9% inter-phase drift), so variant selection needs a microbenchmark.

| variant | implementation |
|---|---|
| `v0_develop` | `schedule(dynamic) collapse(2)` + per-pixel atomics (develop @ `bdf1f6f`) |
| `v1_plain_omp` | row-wise `omp parallel for`, scalar ternary (the #341 fix) |
| `v2_omp_eigen` | row-wise `omp parallel for` + per-row Eigen `select()` |
| `v3_eigen_whole` | whole-array Eigen `select()`, no OpenMP |

Bit-identity vs `v0` is asserted for every variant. A parallel row-wise
`memcpy` between reps doubles as a memory-bandwidth probe.

## Result (2026-07-28, 16 threads, container GCC 13.3, `-O2 -g -DNDEBUG`)

See `artifacts/microbench_rtc_normalize_20260728.txt` for the full log.

| variant | median / call | vs v0 |
|---|---|---|
| v0_develop | 34.118 s | 1x |
| v1_plain_omp | 0.127 s | **269x** |
| v2_omp_eigen | 0.126 s | 271x |
| v3_eigen_whole | 0.908 s | 38x |

v1 and v2 are indistinguishable: both run at the memory-bandwidth roof
(7.44 GB of traffic in 0.127 s = 58.6 GB/s vs the 57.4 GB/s parallel
memcpy reference), so the vectorization that Eigen's branch-free
`select()` enables buys nothing — the pass is bandwidth-bound, as #341
argued. v3 shows why a "just use Eigen" rewrite must keep the OpenMP
loop: Eigen does not thread coefficient-wise expressions.

## Usage (from `isce3-benchmark/`)

```sh
docker compose run --rm --no-deps dev bash poc/rtc_normalize_microbench/run.sh              # full size
docker compose run --rm --no-deps dev bash poc/rtc_normalize_microbench/run.sh 2000 2000    # smoke
```
