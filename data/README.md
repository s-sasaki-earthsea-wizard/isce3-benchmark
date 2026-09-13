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

## ALOS-2 PALSAR-2 Kujukuri stack (Chiba, Japan) — L-band InSAR / loop closure

JAXA publishes a free multi-date ALOS-2 L1.1 CEOS stack as "sample products",
which is enough for a triangle network (loop closure), not just a single pair:

- `[SAR time series] Chiba` — Stripmap 3 m (UBS), HH, frame 0700 **ascending**,
  **12 acquisitions** 2014-09 to 2018-01, ~6.45 GB per zip.
- `[SAR interferometry] Chiba` — same area, frame 2900 **descending** pair
  (2015-01-15, 2016-03-10), ~5.68 GB per zip.

Source page: https://www.eorc.jaxa.jp/ALOS-2/en/doc/sam_index.htm
Terms: JAXA "Terms for Use" — read before publishing derived products.

```bash
fetch/fetch_alos2_kujukuri.sh --set asc            # all 12 ascending scenes
fetch/fetch_alos2_kujukuri.sh --set asc --first 3  # smoke-test subset
fetch/fetch_alos2_kujukuri.sh --set desc           # descending pair
fetch/fetch_alos2_kujukuri.sh --list               # print the job list only
```

Sequential (one FTP stream), resumable (`curl -C -` keeps `.part` files), and
size-verified against the server's `Content-Length` before a `.part` is
promoted. Re-running skips files that are already complete. One log line per
file, so the run can be watched with `tail -f data/ALOS2-kujukuri/download.log`.

Each zip holds CEOS files whose names already match the globs in isce3's
`share/nisar/examples/alos2_to_nisar_l1.py` (ALOS-2 L1.1 stripmap -> NISAR
RSLC HDF5), e.g. for 2015-10-20:

```
VOL-ALOS2076070700-151020-UBSR1.1__A
LED-ALOS2076070700-151020-UBSR1.1__A       <- leader
IMG-HH-ALOS2076070700-151020-UBSR1.1__A    <- 6.44 GB image
TRL-ALOS2076070700-151020-UBSR1.1__A
summary.txt
BRS-HH-ALOS2076070700-151020-UBSR1.1__A.jpg
```

Note that converter is Stripmap-only (one `IMG-<pol>-<pattern>` per
polarization); ScanSAR granules would not match.

### Processing chain (all C(12,2) = 66 pairs → GUNW → loop closure)

```bash
make alos2-convert     # zips -> data/ALOS2-kujukuri/rslc/YYYYMMDD.h5 (v0.25.16 build, ~5 min/scene)
make alos2-network     # 66 runconfigs + pairs_ALOS2_kujukuri.json in configs/alos2_kujukuri/
make alos2-run         # sequential GPU batch -> data/ALOS2-kujukuri/gunw/<pair>/product.h5
```

- **Full scenes, no crop.** An ALOS-2 Ultra-fine scene is 21184 x 37914 px
  (0.8 Gpx) = half a NISAR frequency-A RSLC frame (30400 x 53254, 1.6 Gpx),
  so each pair costs about half of the validated NISAR chain
  (nisar-displacement `run_gunw_batch.sh`: ~33 GB RSS, ~150 GB scratch,
  75 min CPU per NISAR pair). `tools/crop_rslc.py` can cut a sub-scene
  around a geographic point if that is ever wanted.
- **Same processor as the NISAR closure study.** The template
  `configs/insar_alos2_kujukuri_template.yaml` is the nisar-displacement
  DES118_071 template with sensor-forced changes only (looks 8x12 + 4x4,
  GUNW 80 m / wrapped 20 m, EPSG 32654, `split_main_band` ionosphere, no
  TEC / water mask, GPU on). Every pair uses the identical runconfig apart
  from the two RSLC paths and the run name.
- **Layout.** `rslc/` holds the converted RSLCs (6.47 GB each, complex64);
  `ceos/<YYMMDD>/` keeps the leader/volume/trailer files (the 6.4 GB image
  file is deleted after a successful conversion, the zip stays); `gunw/`
  holds one directory per pair with `product.h5`, `insar.log`,
  `console.log` and a `.complete` marker; `logs/` has the fetch / convert /
  runner logs. `configs/alos2_kujukuri/pairs_ALOS2_kujukuri.json` is the
  manifest nisar-displacement's `closure_network.py` reads.
