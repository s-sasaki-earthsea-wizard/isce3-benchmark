# isce3-benchmark

Benchmark and profiling harness for [isce3](https://github.com/isce-framework/isce3) InSAR
workflows on GPU. Sibling to the [mintpy-benchmark](https://github.com/s-sasaki-earthsea-wizard/mintpy-benchmark)
project; same intent, different upstream.

The goal is to surface bottlenecks in the GPU/CPU pipeline that are reproducible enough
to file as upstream RFC issues against isce3.

## Scope

- isce3 source is **read-only** from this repo. CPU vs GPU is toggled via
  `worker.gpu_enabled` in the YAML runconfig — no upstream patches are required to
  benchmark either path.
- Deliverable for the first milestone is an **RFC issue**, not a patch.

## Layout

```
isce3-benchmark/
├── Dockerfile              # CUDA 12.8 + isce3 deps via micromamba
├── docker-compose.yml      # GPU passthrough, host isce3 source mount
├── docker/                 # Image build assets (env file, entrypoint)
├── Makefile                # build / smoke / bench / profile / report targets
├── scripts/                # Shell harness: build, run_bench, profile_*
├── configs/                # Benchmark YAML runconfigs (CPU/GPU pairs)
│   └── micro/              # Single-stage minimal repro configs
├── tools/                  # Python: parse timing, compare runs, plot
├── fetch/                  # Data acquisition scripts
├── data/                   # Input data (gitignored, large)
├── reports/                # Commit-pinned markdown reports + figures
└── logs_<host>/            # Run artifacts (gitignored, machine-dependent)
```

## Host requirements

- NVIDIA GPU with CUDA 12.8+ support (sm_120 = Blackwell tested on RTX 5080)
- NVIDIA driver ≥ 555 and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  configured for Docker
- Docker 25+ with `docker compose`
- The isce3 source tree checked out somewhere on the host (path goes in `.env`)

## Quickstart

```bash
cp .env.example .env
$EDITOR .env                       # set ISCE3_SRC, GPU/thread counts
make env-check                     # verify Docker + NVIDIA runtime + .env
make build                         # build the dev image
make isce3                         # cmake-build isce3 inside the container
make data-ree                      # stage REE synthetic fixtures from isce3/tests
make smoke                         # tiny CPU+GPU end-to-end run
```

After a smoke run, results land in `$BENCH_LOG_DIR` and can be summarised with
`make report`.

## Data

| Stage | Source | Status |
|-------|--------|--------|
| 0     | REE synthetic (from `isce3/tests/data/`) | Initial smoke target |
| 1     | Sentinel-1 IW pair via Copernicus / ASF | Once REE pipeline is green |
| 2     | Curated dataset published on Zenodo     | Once Sentinel-1 run succeeds |

See [data/README.md](data/README.md) for fetch instructions per stage.

## Workflow targets

isce3 GPU paths exist for: `focus` (RSLC), `geo2rdr`, `rdr2geo`, `crossmul`,
`dense_offsets`, `geocode_insar`, `resample_slc`, `baseline`. Orchestrators
`gslc`, `gcov`, `insar` are CPU-only at the orchestration layer but invoke
GPU-enabled stages internally.

Each benchmark scenario lives in `configs/` as a CPU+GPU pair of runconfigs that
differ only in `worker.gpu_enabled`. Single-stage micro-benchmarks for upstream
issue minimal-repros live in `configs/micro/`.

## Reports

Every published measurement run pins:

- the isce3 commit SHA used to build
- the host CPU / GPU / driver / CUDA toolkit versions
- the runconfig file path

Reports live under `reports/` as dated markdown files.
