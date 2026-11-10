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
- NVIDIA driver ≥ 570 (the CUDA 12.8 minimum on Linux) and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
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
| ALOS-2 | JAXA Kujukuri L1.1 sample stack (12 dates) | 66/66 GUNW pairs done, see reports |
| Capella | Mexico City stripmap SICD pair (open data, RGZERO) | Converted + verified, see `make capella-*` |

See [data/README.md](data/README.md) for fetch instructions per stage.

### Data credits

The reports, figures and quicklooks here are derived from the datasets below;
none of their raw data is redistributed. Each notice is the one the provider
asks for, quoted as given.

- **Capella Space Open Data**, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  ([terms](https://www.capellaspace.com/legal/data-licensing)):
  © Capella Space Corp, All Rights Reserved.
  Modified here: SICDs converted to NISAR RSLC and processed to interferograms,
  GCOV / RTC gamma0, and the figures and quicklooks derived from them.
- **Copernicus DEM GLO-30**
  ([licence](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM)):
  produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus
  Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European
  Union and ESA; all rights reserved.
- **ALOS-2 PALSAR-2 sample products**
  ([JAXA](https://www.eorc.jaxa.jp/ALOS-2/en/doc/sam_index.htm)): the original
  data are provided by JAXA as the ALOS-2 sample product. ©JAXA
- **Sentinel-1**: Contains modified Copernicus Sentinel data 2025-2026.
- **NISAR** (NASA, distributed by the ASF DAAC): free and open,
  [CC0](https://creativecommons.org/publicdomain/zero/1.0/) under the
  [NASA ESDIS data use guidance](https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance),
  which asks that NASA be acknowledged and the datasets cited. Source: NASA.
  Dataset: NISAR Provisional Range Doppler Single Look Complex Product
  (Version 1), <https://doi.org/10.5067/NIL1RSLC-P1>. The official L2 GCOV
  and GUNW products used as references are NASA products of the same
  release (CRID `P05023`).

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

Sensor ingest beyond NISAR/ALOS-2: `tools/sicd_to_nisar_rslc.py` repackages an
RGZERO/INCA SICD (Capella stripmap) into a NISAR RSLC; the geometry is
cross-checked against sarkit by `tools/sicd_rslc_geometry_check.py` and the
SLC phase convention is measured by `tools/rifg_fringe_rate.py`
(`reports/2026-09-capella-sicd-stage-u0.md`). Non-L/S-band RSLCs need
`scripts/run_insar_xband.py` in front of `insar.py` (see the report, §5).
The same converter with `--radiometry beta0` feeds isce3's GCOV (RTC) workflow;
that path is cross-checked against MultiRTC in
`reports/2026-09-capella-rtc-gcov-vs-multirtc.md` (`make capella-gcov`,
`multirtc-setup`, `capella-multirtc`, `capella-rtc-compare`). The three
MultiRTC radar-grid defects found there are fixed by a candidate upstream
patch, `patches/multirtc/sicd-rgzero-radar-grid.patch`, verified on the same
data in `reports/2026-11-multirtc-sicd-grid-fix.md`
(`make multirtc-patched-setup`, `capella-multirtc-patched`,
`multirtc-grid-survey-patched`).
