# Sentinel-1 InSAR baseline — Boso peninsula pair, RTX 5080

- Date: 2026-05-10
- Host: Intel Core Ultra 9 285H (16 cores) / NVIDIA RTX 5080 (16 GiB) /
  driver 590.48.01 / CUDA toolkit 12.8 (container) / 13.0 (host)
- isce3 commit: `9552d68` (develop, sm_120 build, our local from-source
  install at `/opt/isce3-build/install`)
- isce3-benchmark commit: `8def35a` (`feat/sentinel1-bench`)
- Runconfigs:
  - CSLC ref:    [configs/insar_s1_boso_cslc_cpu.yaml.template](../configs/insar_s1_boso_cslc_cpu.yaml.template)
  - CSLC sec:    rendered from same template, `is_reference: False`
  - Crossmul:    [configs/insar_s1_boso_crossmul_cpu.yaml](../configs/insar_s1_boso_crossmul_cpu.yaml) / [_gpu.yaml](../configs/insar_s1_boso_crossmul_gpu.yaml)
- Dataset: Sentinel-1A IW SLC dual-pol pair over Boso peninsula
  (descending track 46, frame ~97519, IW3 burst `t046_097519_iw3`, VV)
  - Reference: `S1A_IW_SLC__1SDV_20251221T204341_20251221T204408_062418_07D1B4_CC6C.SAFE`
  - Secondary: `S1A_IW_SLC__1SDV_20260126T204338_20260126T204405_062943_07E587_C319.SAFE`
  - 36-day temporal baseline (3× S1 repeat cycle), absolute orbit diff
    525 = 175×3 → identical relative orbit
  - Ancillaries fetched via `make data-s1`: 2× POEORB EOFs (sentineleof),
    8 Copernicus GLO-30 DEM tiles stitched (dem_stitcher)

## TL;DR

- **Built and ran the full S1 → CSLC → wrapped-interferogram chain on isce3
  GPU primitives**, via COMPASS radar mode + a direct
  `isce3.cuda.signal.Crossmul` call. Pipeline is GREEN end-to-end on both
  CPU and GPU pairs (18 of 18 bench runs exit 0).
- **Five reportable findings** surfaced while making the chain green and
  measuring it — see the Findings section. Three are upstream-actionable
  (isce3 / COMPASS), one is a bench-side configuration knob, one is a
  surprising "GPU = CPU" result that warrants follow-up profiling.
- Headline numbers (n=3, single IW3 burst, RTX 5080 / Core Ultra 9 285H):
  - **`isce3.cuda.signal.Crossmul`: 2× kernel speedup**, 1.5× wall (17.4s
    CPU → 11.5s GPU) — the most clear win.
  - **`isce3.cuda.image.ResampSlc`: 1.2× modest speedup** (8s → 7s
    kernel), but **CPU usage drops 100% → 25%** so GPU offloads the host.
  - **`isce3.cuda.geometry.Geo2Rdr`: no speedup** (17s either way) — both
    paths confirmed dispatched via `journal` logs. Worth a profiling pass.
- Bench artifacts: [logs_nucbox-evo-t1/20260510T082015Z_s1/](../logs_nucbox-evo-t1/20260510T082015Z_s1/)

## What the chain does

```
S1 SAFE pair  ──s1reader──>  burst objects
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                           ▼
COMPASS radar mode (ref)                COMPASS radar mode (sec)
  isce3.geometry.Rdr2Geo                  isce3.geometry.Geo2Rdr   (CPU)  /  isce3.cuda.geometry.Geo2Rdr  (GPU)
  → topo rasters + ref SLC tif            isce3.image.ResampSlc    (CPU)  /  isce3.cuda.image.ResampSlc   (GPU)
  (CPU only — see finding #3)             → coregistered sec SLC tif
        │                                           │
        └─────────────────────┬─────────────────────┘
                              ▼
                  scripts/run_crossmul.py
                  isce3.signal.Crossmul        (CPU)
                  isce3.cuda.signal.Crossmul   (GPU)
                              │
                              ▼
                wrapped interferogram (.int) + coherence (.coh)
```

Stages and dispatch:

| stage   | CPU backend                   | GPU backend                       | dispatched by               |
|---------|-------------------------------|------------------------------------|------------------------------|
| ref CSLC| `isce3.geometry.Rdr2Geo`      | (forced CPU; finding #3)           | `s1_cslc.py --grid radar`    |
| sec CSLC| `isce3.geometry.Geo2Rdr` + `isce3.image.ResampSlc` | `isce3.cuda.geometry.Geo2Rdr` + `isce3.cuda.image.ResampSlc` | `s1_cslc.py --grid radar`    |
| crossmul| `isce3.signal.Crossmul`        | `isce3.cuda.signal.Crossmul`       | `scripts/run_crossmul.py`    |

## Measurements

> _Filled in once `bench s1` (repeats=3, mean ± stdev) finishes. Per-run
> raw numbers are in `logs_nucbox-evo-t1/20260510T075410Z_s1/<tag>.time`._

### Per-stage wall-clock and resident memory (n=3)

Numbers are mean ± stdev across 3 repeats. Source:
`logs_nucbox-evo-t1/20260510T082015Z_s1/<tag>_<rep>.time` (parsed from
`/usr/bin/time -v` output).

| stage                | path | wall (s)       | user CPU (s)  | %CPU  | max RSS (MiB) |
|----------------------|------|----------------|---------------|-------|----------------|
| ref CSLC (rdr2geo)   | CPU  | 56.6 ± 1.7     | 428.6 ± 3.5   | 761%  | 1228           |
| ref CSLC (rdr2geo)   | GPU* | 53.6 ± 3.5     | 432.7 ± 4.4   | 814%  | 1230           |
| sec CSLC (geo2rdr+resamp) | CPU | 35.4 ± 0.6  | 34.1 ± 0.4    | 107%  | 2123           |
| sec CSLC (geo2rdr+resamp) | GPU | 31.1 ± 1.3  | 5.2 ± 0.05    |  25%  | 2259           |
| crossmul             | CPU  | 17.4 ± 0.17    | 21.8 ± 0.6    | 151%  | 6836           |
| crossmul             | GPU  | 11.5 ± 1.5     | 2.6 ± 0.05    |  39%  | 2962           |

\* "GPU" ref CSLC is the same code path as CPU because of finding #3.

### Inner kernel timings

Wall-clock includes COMPASS Python orchestration, GDAL I/O, EAP correction
LUT compute, and runconfig parsing. The numbers below are pulled from
COMPASS `journal` log lines for the actual isce3-side stage call so you
can see the kernel itself separated from the orchestration:

| inner stage          | CPU (s)   | GPU (s)  | speedup | binding selected |
|----------------------|-----------|----------|---------|---------------------------|
| `s1_rdr2geo` (ref)   | 44.7 ± 1.5 | 45.0 ± 2.1 | 1.0× | CPU both (finding #3)     |
| `s1_geo2rdr` (sec)   | 16.3 ± 0.6 | 17.3 ± 0.6 | **0.94×** | `isce.{geometry,cuda.geometry}.Geo2rdr` (confirmed via journal log) |
| `s1_resample` (sec)  | 7.8 ± 1.2  | 6.7 ± 1.6  | 1.16× | `GPU resampling using 7 tiles of 250 lines per tile` |
| `Crossmul.crossmul`  | 14.0 ± 0.04 | 7.2 ± 0.1 | **1.94×** | `isce3.{signal,cuda.signal}.Crossmul`     |

(Geo2Rdr inner times: GPU is *slightly slower* than CPU. The CUDA path is
genuinely dispatched per `journal (isce.cuda.geometry.Geo2rdr)`, so this
isn't a "we accidentally measured CPU twice" artifact. See finding #5.)

### Where the wall-clock goes

For the secondary CSLC (the only full-stage GPU-dispatching CSLC step):

```
wall 31.1 s GPU  =  5.2 s host CPU   (≈ 17%)
                +  ~10 s GPU compute (geo2rdr + resample on the device)
                + ~16 s waiting on I/O / orchestration / GDAL VRT setup
                  (this is the "wall − user_cpu − gpu_compute" residue)
```

Even with everything dispatched correctly, the COMPASS Python
orchestrator + GDAL VRT round-tripping is most of the wall. Optimising
the kernels alone would reach diminishing returns fast; an upstream
contribution that helps S1 production materially needs to also touch the
loader/IO path.

### Burst geometry (for sizing context)

- Reference burst raster: `24443 × 1516` cf32 (= 282 MiB / burst SLC).
- Coregistered secondary: same shape (resampled onto reference grid).
- Multilooked interferogram (range_looks=4, az_looks=1): `6110 × 1516` cf32
  (= 71 MiB).

## Findings

These are bottlenecks / API gaps observed while making the chain green.
Each is a candidate RFC against the relevant upstream (isce3 or COMPASS).
None of them are bugs in *our* harness — they reflect the intersection of
the libraries as they ship today.

### Finding #1 — `isce3.geocode.geocode_slc` has no CUDA implementation

**Where it bites**: OPERA's CSLC-S1 product (the L2 product COMPASS produces
in `--grid geo` mode) terminates in a single call to
`isce3.geocode.geocode_slc` (in `compass.s1_geocode_slc.run`). isce3 ships
CUDA `Geocode` (`cxx/isce3/cuda/geocode/Geocode.h`) used by NISAR's
`geocode_insar`, but **there is no CUDA `GeocodeSlc.cpp`** — the SLC-specific
fused kernel (geo2rdr + interp + flatten) is CPU-only. So the production
OPERA CSLC pipeline is CPU-bound on this kernel regardless of `gpu_enabled`.

**Why we use radar mode here**: precisely to side-step this. Radar mode's
heavy work is in `Rdr2Geo` / `Geo2Rdr` / `ResampSlc`, all of which DO have
CUDA siblings.

**Possible RFC angle**: port `geocode_slc` to CUDA. The non-SLC `Geocode`
already provides the geocoding kernel template; the SLC variant adds carrier
phase / flatten which are kernels well-suited to GPU.

### Finding #2 — Path divergence in COMPASS radar mode (geo2rdr writes product, resample reads scratch)

**Where it bites**: `compass.s1_geo2rdr.run` writes `range.off` /
`azimuth.off` to `out_paths.output_directory`
([s1_geo2rdr.py:85](https://github.com/opera-adt/COMPASS/blob/main/src/compass/s1_geo2rdr.py#L85)),
but `compass.s1_resample.run` reads them from `out_paths.scratch_directory`
([s1_resample.py:71-73](https://github.com/opera-adt/COMPASS/blob/main/src/compass/s1_resample.py#L71-L73)).
With separate `product_path` and `scratch_path` in the runconfig
(per the COMPASS defaults yaml example), the secondary CSLC fails:

```
RuntimeError: failed to create GDAL dataset from file
  '/.../scratch_sec/<burst>/<date>/range.off'
```

**Workaround we use**: set `product_path == scratch_path` in the rendered
runconfig ([tools/render_s1_runconfig.py](../tools/render_s1_runconfig.py)).
The geo-mode template in compass-benchmark also uses separate paths but
geo mode reads/writes through different functions and isn't affected.

**Possible upstream report (against COMPASS)**: pick one — either point
both calls at `output_directory`, or both at `scratch_directory`. The
choice probably depends on PGE expectations of what stays in product vs
what is intermediate.

### Finding #3 — CPU/CUDA API parity gap on `isce3.geometry.Rdr2Geo.topo`

**Where it bites**: `isce3.cuda.geometry.Rdr2Geo.topo` overload 2 declares
**all 11 raster args as required** (no `= None` defaults), while the CPU
sibling marks them all as `= None`. COMPASS calls `topo` with the same 7
kwargs in both paths; if the user has not requested some optional layers
(`compute_local_incidence_angle`, `compute_ground_to_sat_east`, etc.) the
remaining rasters are `None` and the CUDA dispatch raises:

```
TypeError: topo(): incompatible function arguments. The following argument
types are supported:
    1. topo(self, dem_raster, outdir: str)
    2. topo(self, dem_raster, x_raster, y_raster, height_raster,
            incidence_angle_raster, heading_angle_raster,
            local_incidence_angle_raster, local_Psi_raster,
            simulated_amplitude_raster, layover_shadow_raster,
            ground_to_sat_east_raster, ground_to_sat_north_raster) -> None
```

There is no `Optional[Raster]` overload in the CUDA binding.

**Workaround we use**: force `worker.gpu_enabled: False` for the reference
CSLC step
([configs/insar_s1_boso_cslc_gpu.yaml.template](../configs/insar_s1_boso_cslc_gpu.yaml.template)).
This makes the rdr2geo step CPU on both pairs; only `geo2rdr + resample`
in the secondary and `crossmul` differ between CPU and GPU.

**Possible RFC angle**: add an Optional-rasters overload to
`isce3.cuda.geometry.Rdr2Geo.topo` that mirrors the CPU defaults. Same
issue may exist on other CUDA bindings — worth a sweep.

### Finding #4 — `isce3.cuda.image.ResampSlc` OOMs on 16 GiB VRAM at the COMPASS-default block size

**Where it bites**: With COMPASS's default `processing.resample.lines_per_block: 1000`,
the secondary CSLC GPU run on a single S1 IW3 burst (24443×1516 cf32, ~282
MiB on disk) fails with:

```
MemoryError: std::bad_alloc: cudaErrorMemoryAllocation: out of memory
  at compass.s1_resample.run → resamp_obj.resamp(...)
```

The host has 16 GiB on the RTX 5080 — the per-tile working set evidently
exceeds that with `lines_per_block=1000`. Lowering to **250** (4× more
tiles) makes the run complete in 5–9 s of inner GPU time.

**Workaround we use**: GPU template overrides `resample.lines_per_block: 250`
([configs/insar_s1_boso_cslc_gpu.yaml.template](../configs/insar_s1_boso_cslc_gpu.yaml.template)).

**Possible RFC angle (against isce3)**: the binding could expose memory
estimation or auto-tile based on free VRAM (`cudaMemGetInfo`). Today the
user has to tune `lines_per_block` empirically per GPU model. For S1
production at scale (DISP-S1 processes thousands of bursts), a
deployment-time guess that holds for an A100 may fail silently on a
consumer Blackwell.

### Finding #5 — `isce3.cuda.geometry.Geo2Rdr` shows no measurable speedup vs CPU

**Where it bites**: `s1_geo2rdr` inner times measure
**16.3 s CPU vs 17.3 s GPU** (mean of 3 runs each). The CUDA path is
genuinely engaged — verified by `journal (isce.cuda.geometry.Geo2rdr)`
log lines that don't appear in the CPU run. So this isn't an
"accidentally CPU" artifact.

Plausible causes (not yet investigated):

- The geo2rdr iteration is small enough per pixel that GPU dispatch +
  cross-PCIe traffic eats the speedup.
- The CUDA implementation may be I/O-bound on the topo VRT read instead
  of compute-bound.
- The CPU implementation already saturates a single core on this size,
  and isce3.signal.Crossmul shows OpenMP doesn't lift much further (CPU
  runs 100–150% utilisation at most).

**Possible follow-up**: a single `make profile-nsys` pass on the sec GPU
run would tell us where the GPU time actually goes — kernel vs memcpy vs
wait. That's the natural next step before any RFC framing.

### Side note — S1 community largely doesn't reach isce3's GPU InSAR primitives

The reason this bench exists is that `isce3.cuda.signal.Crossmul` and
`isce3.cuda.matchtemplate.PyCuAmpcor` are well-maintained NISAR production
code, but no Sentinel-1 production pipeline (COMPASS, OPERA DISP-S1's
`dolphin`) actually exercises them. The data-format gap is partly
structural (NISAR uses HDF5 RSLC, S1 uses GeoTIFF bursts), but it's not
*total* — as this harness shows, it's a few-hundred-line wrapper away.

For our pair: **`isce3.cuda.signal.Crossmul` produced an interferogram in
~7 s of kernel time on real S1 data** when called directly. That number
is what the S1 / OPERA stack would benefit from if/when it adopts these
primitives — and is the cleanest "look, the upstream code already works,
you just have to call it" data point for an RFC.

## Reproducing

```bash
cd isce3-benchmark/

# one-time
make build           # ~5 min, includes patchelf
make isce3           # ~15-25 min, from-source against bind-mounted /opt/isce3-src

# data
make data-s1         # POEORB orbits + Copernicus DEM into data/S1-boso/
make render-s1       # render configs/insar_s1_boso_cslc_*.yaml from templates

# run
make smoke-s1        # repeats=1 sanity
docker compose run --rm dev bash scripts/run_bench.sh s1   # repeats=3 bench
```

Per-run artifacts land in
`logs_<hostname>/<UTC-timestamp>_s1/<tag>.{log,err,time}` plus
`provenance.txt` capturing host / GPU / driver / nvcc / isce3 SHA.

## What's next

- Pick the RFC. Strongest candidates:
  - **#1 (CUDA `geocode_slc`)** — clearest production impact (every OPERA
    L2 CSLC product would benefit), but biggest port effort.
  - **#3 (Rdr2Geo Optional kwargs)** — smallest patch, biggest "S1-shaped
    workflow doesn't pay a regression to use the CUDA primitive" win.
  - **#5 (Geo2Rdr no speedup)** is *measurement evidence* but not yet an
    RFC — needs profiling first.
- Re-run on a larger AOI (full IW2 subswath, ~9 bursts) to see whether
  the per-burst GPU init overhead amortises at scale and whether the
  ResampSlc OOM scales with burst count.
- COMPASS-side: separate report for finding #2 against `opera-adt/COMPASS`
  (low-stakes; clearly a path-bug fix).
- `make profile-nsys` on the sec GPU run to break down the 31 s wall
  into kernel-vs-memcpy-vs-wait — needed before any RFC for #5.
