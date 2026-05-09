# Benchmark data

This directory holds input data for benchmark runs. Everything except this README
and `.gitkeep` is git-ignored — datasets are too large and machine-specific to track.

## Stage 0 — REE synthetic (smoke test)

REE = NISAR's Radar Echo Emulator. The isce3 test suite ships small synthetic
products that exercise the full RSLC → GSLC → GUNW pipeline.

```bash
make data-ree
# expands isce3/tests/data/REE_*.h5 (and ancillaries) into data/REE/
```

After this completes, `configs/smoke_ree_rslc_{cpu,gpu}.yaml` should resolve.

## Stage 1 — Sentinel-1 IW pair (real-data benchmark)

Once the REE smoke run is green, switch to a Sentinel-1 IW SLC pair from
Copernicus / ASF for representative I/O sizes. License: Copernicus open access
(re-distributable with attribution).

Suggested pair (TBD once REE is validated):
- Reference: `S1A_IW_SLC__1SDV_<...>` (≈ 5 GB)
- Secondary: `S1A_IW_SLC__1SDV_<...>` (≈ 5 GB)
- DEM: Copernicus DEM 30 m for the AOI
- Orbit files: precise orbits (POEORB) from ESA

`fetch/fetch_sentinel1.py` will be added at this stage.

## Stage 2 — Zenodo publication

After Stage 1 measurements stabilise, the curated dataset (subset, fixed
metadata, runconfig templates) gets published to Zenodo under the user's
account so that upstream RFC/PR readers can reproduce results with a single
DOI fetch.
