# Benchmark runconfigs

Each scenario is a **pair** of YAML runconfigs that differ only in
`worker.gpu_enabled` (and `worker.gpu_id` if multi-GPU). Naming convention:

```
<scenario>_<cpu|gpu>.yaml
```

`scripts/run_bench.sh` discovers scenarios by listing `<scenario>_cpu.yaml` and
running both halves of the pair.

## Filling in paths

The runconfigs reference input files via absolute container paths. Inside the
container, the bind mounts are:

| Container path | Host source        |
|----------------|--------------------|
| `/data`        | `${BENCH_DATA_DIR}` |
| `/logs`        | `${BENCH_LOG_DIR}`  |
| `/opt/isce3-src` | `${ISCE3_SRC}` (read-only) |

So a REE product staged into `./data/REE/REE_L0B_out17.h5` is referenced as
`/data/REE/REE_L0B_out17.h5` inside a runconfig.

## Workflow dispatch

`run_bench.sh` reads `runconfig.name` from each YAML and dispatches:

| `runconfig.name`              | Python entry point                     | Notes |
|-------------------------------|----------------------------------------|-------|
| `focus`                       | `nisar.workflows.focus`                | NISAR L0B → RSLC |
| `gslc` / `gcov`               | `nisar.workflows.{gslc,gcov}`          | NISAR orchestrators |
| `insar`                       | `nisar.workflows.insar`                | NISAR full InSAR |
| `cslc_s1_workflow_default`    | `compass.s1_cslc`                      | OPERA CSLC for Sentinel-1 |
| `crossmul_s1`                 | `scripts/run_crossmul.py`              | Direct isce3 crossmul on radar-grid bursts |

## Existing scenarios

- `smoke_ree_rslc_{cpu,gpu}.yaml` — REE synthetic L0B → RSLC focus, smoke test.
- `insar_s1_boso_cslc_{cpu,gpu}.yaml` — Sentinel-1 SAFE pair (Boso peninsula,
  desc track 73, Dec 2025 + Jan 2026) → CSLC via COMPASS radar mode. Requires
  `make data-s1` to have populated `data/S1-boso/` with orbits + DEM.
- `insar_s1_boso_crossmul_{cpu,gpu}.yaml` — wrapped interferogram from the
  pair of resampled radar-grid bursts produced by the CSLC step. Calls
  `isce3.signal.Crossmul` (CPU) or `isce3.cuda.signal.Crossmul` (GPU)
  directly via `scripts/run_crossmul.py`.

## micro/

Single-stage minimal-repro configs intended to attach to upstream RFC
issues. Populated as we identify specific bottlenecks worth filing.
