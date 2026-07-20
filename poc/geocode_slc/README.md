# geocode_slc CUDA PoC microbenchmark

Measurement PoC for a prospective CUDA port of
`isce3::geocode::geocodeSlc` ([issue #11], gates the second upstream
RFC). **This is not the port** — it isolates two selected compute
patterns of `cxx/isce3/geocode/geocodeSlc.cpp` (candidate GPU kernels;
NOT established as the dominant cost of the real call) as minimal CUDA
kernels plus OpenMP CPU references on synthetic, realistically shaped
data, so that GPU speedup and precision claims in the RFC rest on
measurement rather than estimation.

[issue #11]: https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/11

## What is modelled

| PoC kernel | Real counterpart | Pattern |
|---|---|---|
| `interpKernel` | `interpolate()` (`geocodeSlc.cpp:409`) + `Sinc2dInterpolator::_sinc_eval_2d` | per output pixel: irregular 9×9 chip gather, per-row doppler demod, 8×8 weighted sum against the 8192×8 normalized sinc table, doppler add-back |
| `flattenKernelF64` | `carrierPhaseRerampAndFlatten()` (`geocodeSlc.cpp:299`) | per output pixel: carrier poly eval + flatten phase `4π/λ·sRng` (~2×10⁸ rad), fp64 `sincos`, complex rotation |
| `flattenKernelF32` | (naive single-precision variant) | measures the fp64→fp32 speed delta AND the phase error from evaluating ~2×10⁸ rad in fp32 |

Default problem shape mirrors the Boso S1 IW3 burst from
[`reports/2026-05-geocode-slc-profile.md`](../../reports/2026-05-geocode-slc-profile.md):
radar block 1500×24000 cf32 (~275 MiB), output geogrid 1046×645. The
geo→radar index map is a rotated, mildly nonlinear affine whose image
sits inside the radar grid, with NaN outside an elliptical footprint
(emulating invalid geo2rdr pixels).

## What is measured

- CPU (OpenMP) vs GPU kernel-only wall time per kernel (best AND
  median of N reps)
- **implementation-overhead A/B**: the fused CPU reference vs an
  "orig-style" CPU reference that reproduces the real `interpolate()`
  call pattern (per-pixel heap-allocated chip + virtual interpolator
  dispatch) with identical arithmetic — bounds how much of the real
  code's CPU time is implementation overhead rather than algorithm
- H2D/D2H transfer bytes, times, effective bandwidth
- transfer-inclusive GPU end-to-end time
- CPU-vs-GPU agreement (interp: max relative error; flatten fp64: max
  applied-rotation phase error)
- fp32-vs-fp64 flatten phase error, unwrapped (analytic) and wrapped
  (from GPU outputs) — the "is fp64 truly required?" datum
- working-set size (bounding box and row-span of touched radar chips)

## How to read the numbers (do not quote out of context)

- The kernel speedups measured here (interp ~100x, flatten ~65x on
  RTX 5080) describe **only these two phases**, which together account
  for ~53 ms of CPU time against the ~29 s measured for the full
  `geocode_slc` call. They say "these kernels are GPU-friendly", not
  "geocode_slc gets ~100x faster". Never cite them without this
  context.
- The CPU references are **faithful reimplementations of the isce3
  loops, not the isce3 binaries**: they preserve the arithmetic but
  drop the per-pixel `Matrix` chip heap allocation and the virtual
  interpolator dispatch of the real `interpolate()`. CPU times here
  therefore understate the real implementation's cost.
- Nothing here extrapolates to the geo2rdr phase (per-pixel Newton
  solve with data-dependent iteration counts → warp divergence).
  That phase needs its own PoC/measurement.

## Known simplifications

- The geo2rdr and `carrierPhaseDeramp` phases are **not** modelled.
  Deramp is the same arithmetic pattern as flatten evaluated over the
  input grid; geo2rdr (iterative Newton solve per pixel over orbit
  interpolation) needs its own treatment in the real port.
- The native-doppler `LUT2d` eval is replaced by a bilinear polynomial
  of the same cost class; `LUT2d::contains()` by NaN/bounds checks.
- The GPU interp kernel uses `sincosf` for doppler factors (as a real
  port would) where the CPU code computes `cos`/`sin` in double and
  casts to float, and accumulates row-first; the validation tolerance
  (1e-3 relative) absorbs both.
- Real COMPASS runs process per-burst blocks through GDAL/HDF5; file
  I/O is out of scope here (measured separately in the profile report).

## Usage

From the repo root (host):

```
make poc-geocode-slc          # build + run inside the dev container
```

Or inside the container / any CUDA host:

```
bash poc/geocode_slc/build.sh              # POC_ARCH=sm_80 to cross-build
bash poc/geocode_slc/run.sh                # archives results under /logs
./poc/geocode_slc/geocode_slc_poc --help   # single run, custom sizes
```

`run.sh` executes the baseline geogrid and a `--scale 2` problem-size
point, archiving stdout, `results.csv`, and provenance under
`$BENCH_LOG_DIR/poc_geocode_slc/<UTC timestamp>/`.

Exit code is non-zero if CPU/GPU validation fails (interp rel err
≥ 1e-3 or flatten fp64 phase err ≥ 1e-5 rad).
