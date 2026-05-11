# Bench-shaped verification — CUDA `Rdr2Geo.topo()` optional-rasters fix

End-to-end and isce3-only verification of the proposed binding fix for the
CUDA `Rdr2Geo.topo()` optional-rasters regression. Linked from the upstream
RFC issue: `<TBD: upstream RFC issue URL once filed>`.

- Date: 2026-05-10 (verification runs); 2026-05-11 (write-up)
- Host: Intel Core Ultra 9 285H (16 cores) / NVIDIA RTX 5080 (16 GiB) /
  driver 590.48.01 / CUDA toolkit 12.8 (container)
- isce3 source tree under bench:
  - BEFORE: upstream `develop` HEAD `9552d68` (clean)
  - AFTER:  upstream `develop` HEAD `9552d68` + the two binding commits
    from
    [`s-sasaki-earthsea-wizard/isce3@feat/cuda-rdr2geo-optional-kwargs`](https://github.com/s-sasaki-earthsea-wizard/isce3/tree/feat/cuda-rdr2geo-optional-kwargs)
    ([`3638c4b2`](https://github.com/s-sasaki-earthsea-wizard/isce3/commit/3638c4b2)
    and [`185905d4`](https://github.com/s-sasaki-earthsea-wizard/isce3/commit/185905d4)),
    applied to the working tree before `make isce3`. Recorded in each
    AFTER `provenance.txt` as the develop SHA + 2 modified files.

## Bench-shaped verification

End-to-end Sentinel-1 IW3 burst-pair bench (single burst,
`t046_097519_iw3` over Boso peninsula), n=3 repeats per (path, stage).
Bench script: [scripts/run_bench.sh](../scripts/run_bench.sh) under
`/usr/bin/time -v`.

| run    | gpu_enabled (REF CSLC) | isce3 build                                              | bench result                                                          | log dir                                                                                                    |
|--------|------------------------|----------------------------------------------------------|-----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| BEFORE | `True`                 | upstream `9552d68` clean                                 | 3/3 CPU OK; **1st GPU REF aborted with TypeError**; remainder skipped | [logs_nucbox-evo-t1/20260510T132819Z_s1/](../logs_nucbox-evo-t1/20260510T132819Z_s1/)                      |
| AFTER  | `True`                 | upstream `9552d68` + fork-branch binding commits applied | **18/18 runs Exit status 0**                                          | [logs_nucbox-evo-t1/20260510T140200Z_s1/](../logs_nucbox-evo-t1/20260510T140200Z_s1/)                      |

The BEFORE failure (full traceback in
[`insar_s1_boso_cslc_gpu_1.err`](../logs_nucbox-evo-t1/20260510T132819Z_s1/insar_s1_boso_cslc_gpu_1.err))
is the pybind11 "incompatible function arguments" form. The actual
invoked call shape recorded in the traceback is:

```
kwargs: x_raster=<Raster>, y_raster=<Raster>, height_raster=<Raster>,
        local_incidence_angle_raster=None, layover_shadow_raster=<Raster>,
        ground_to_sat_east_raster=None, ground_to_sat_north_raster=None
```

i.e. seven raster kwargs are passed (some `Raster`, some `None`); the
other five are omitted entirely. The omitted ones have no `= None`
defaults on the CUDA binding, so pybind11 fails the overload match
before any `None → nullptr` conversion is attempted.

For the AFTER run, every single one of the 18 `.time` files records
`Exit status: 0`. Per-run provenance (host / GPU / driver / CUDA toolkit
/ isce3 SHA + working-tree modifications) is captured in each run dir's
`provenance.txt`.

## Side benefit of the fix

With the binding accepting `None` / omitted optional kwargs, the REF
CSLC step dispatches to `isce3.cuda.geometry.Rdr2Geo` instead of falling
back to the CPU sibling. Inner-kernel timings (from `journal` log lines,
isolating the `s1_rdr2geo` stage from Python orchestration):

| inner stage          | CPU (s)   | GPU (s)   | speedup |
|----------------------|-----------|-----------|---------|
| `s1_rdr2geo` (REF)   | 45-48     | 18-19     | ~2.5x   |

Incidental — the upstream RFC is about a binding-layer API contract,
not the speedup. Recorded here so reviewers can see that the workaround
removed by the fix (`gpu_enabled: False` on the REF CSLC step) also
costs measurable wall-clock.

**Caveat**: measured on RTX 5080 (consumer Blackwell, 16 GiB), not on
the NISAR/OPERA deployment targets (A100 / H100, 40-80 GiB). Whether
the ~2.5x ratio holds on a datacenter-class GPU is unverified;
A100 verification is on the project roadmap. The API-contract argument
in the RFC stands independent of GPU class.

## isce3-only repro recap

Pointers to the standalone script and its captured BEFORE / AFTER
outputs (no Sentinel-1, no COMPASS — imports only `isce3` and uses
isce3's own test fixtures):

- script:
  [scripts/repro_cuda_rdr2geo_optional_kwargs.py](../scripts/repro_cuda_rdr2geo_optional_kwargs.py)
- BEFORE artifact (`origin/develop` build):
  [artifacts/before_repro_cuda_rdr2geo_develop.txt](../artifacts/before_repro_cuda_rdr2geo_develop.txt)
- AFTER artifact (fork-branch build):
  [artifacts/after_repro_cuda_rdr2geo_fork.txt](../artifacts/after_repro_cuda_rdr2geo_fork.txt)

Layer 1 (pybind11 docstring inspection) on `develop` HEAD: CPU
`defaults=11`, CUDA `defaults=0`. Layer 2 (runtime call with kwarg
subset `x/y/height` only): CPU `PASS`, CUDA `TypeError`. After the
fix: both layers green on both siblings. Full quoted output is in the
upstream RFC body.

## Reproducing

End-to-end bench (BEFORE / AFTER pattern, requires the upstream fork
branch checked out on the `${ISCE3_SRC}` host path used by `make isce3`):

```bash
cd isce3-benchmark/

# one-time setup
make build           # ~5 min
make isce3           # ~15-25 min, against bind-mounted ${ISCE3_SRC}
make data-s1         # POEORB orbits + Copernicus DEM
make render-s1       # render configs/insar_s1_boso_cslc_*.yaml from templates

# verification cycle (run twice — once on develop HEAD, once with the
# fork-branch commits applied to ${ISCE3_SRC}'s working tree, with
# `make isce3` in between to rebuild)
docker compose run --rm dev bash scripts/run_bench.sh s1
```

isce3-only repro (no Sentinel-1, no COMPASS — just the contract):

```bash
docker compose run --rm dev python scripts/repro_cuda_rdr2geo_optional_kwargs.py
```

Per-run artifacts land in
`logs_<hostname>/<UTC-timestamp>_s1/<tag>.{log,err,time}` plus
`provenance.txt` capturing host / GPU / driver / nvcc / isce3 SHA.

🤖  Assisted-by: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
