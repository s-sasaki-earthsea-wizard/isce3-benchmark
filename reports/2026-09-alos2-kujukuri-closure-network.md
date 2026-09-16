# ALOS-2 Kujukuri: a 12-date, 66-pair GUNW network from free JAXA sample data

**2026-09-13 to 2026-09-16.** This report covers the interferometric
processing only. Loop closure is evaluated in `nisar-displacement`, time
series in the `MintPy` fork; both have handoff notes pointing here.

## Why

The Sentinel-1 Boso bench had a single interferometric pair, so loop
closure was out of reach, and every measurement so far came from one
sensor. JAXA distributes a free 12-date ALOS-2 PALSAR-2 L1.1 stack as
"sample products" — enough acquisitions for a complete network, in L band,
over Japan.

## Data

JAXA PALSAR-2 sample page, `[SAR time series] Chiba`: 12 acquisitions over
Kujukuri (Chiba), Stripmap Ultra-fine (UBS), HH, frame 0700 ascending,
2014-09-09 to 2018-01-09, ~6.45 GB per zip (73 GiB total, 81 min to fetch).
Uniform stack: identical product code, off-nadir 29.5 deg throughout, scene
centres within 12 s of each other, precision orbits embedded, all quality
flags GOOD. Three scene widths appear (21120 / 21184 / 21248 samples); the
rest are 21184 x 37914.

`fetch/fetch_alos2_kujukuri.sh` downloads them (sequential, resumable,
size-verified). `scripts/convert_alos2_kujukuri.sh` converts each to a
NISAR-format RSLC with isce3's bundled
`share/nisar/examples/alos2_to_nisar_l1.py`: ~5 min and 0.3 GB RSS per
scene, 6.47 GB output. The converter's output does not depend on the isce3
build — the 0.26.0-dev and v0.25.16 builds produce RSLCs identical in all
70 datasets except `processingDateTime`.

## Chain

`configs/insar_alos2_kujukuri_template.yaml` is the runconfig validated
against an ASF production GUNW in `nisar-displacement`
(`reports/2026-08-06-gunw-selfrun-validation.md`, isce3 v0.25.16), with only
sensor-forced changes: looks scaled to the 1.43 m x 1.91 m pixels
(crossmul 8x12, phase_unwrap 28x36 total, giving a RUNW pixel of
40.0 x 68.8 m against NISAR's 40.6 x 71 m), `split_main_band` ionosphere on
the single 79.4 MHz band, and no TEC, water mask or weather-model files
because none are distributed for this data. GPU enabled throughout.

Full scenes are processed. An ALOS-2 Ultra-fine scene is 0.8 Gpx, which is
**half** a NISAR frequency-A RSLC frame (30400 x 53254 = 1.6 Gpx), so the
validated chain's per-pair footprint scales down rather than up.
`tools/crop_rslc.py` exists if a sub-scene is ever wanted; it is unused.

`tools/make_alos2_network.py` renders one runconfig per date-ordered pair
plus a manifest in the format `nisar-displacement`'s `closure_network.py`
reads. `scripts/run_alos2_network.sh` runs them sequentially.

## Results

All C(12,2) = 66 pairs completed; the network carries all 220 triangles.

| | |
|---|---|
| temporal baselines | 14 to 1218 d (median 448) |
| per-pair wall | mean 26.3 min, median 25.1, range 23.4-46.6 |
| total compute | 28.5 h over 65 successful runs |
| peak RSS | 17.6 GB |
| scratch | ~70-100 GB per pair, deleted on success |
| products | 209-210 MB each, 11 GB total |

Verified across all 66 products: EPSG 32654, pixel centres on the N*80+40
lattice, `ionospherePhaseScreen` present, valid-pixel fraction 58.1 %
(land and coast; the remainder is ocean). Grid shapes are not uniform —
1005x1022 in 58 pairs, 1005x1020 in 6, 1006x1024 in 2 — which follows from
the three scene widths changing each pair's common footprint. All share the
lattice, so cropping to a common extent is the correct handling.

Representative stage times (`20171017_20180109`, 1480 s total): split
spectrum 283, rubbersheet 146, fine resample 145, unwrap 129 (including a
96 s multilook crossmul), coarse resample 104, crossmul 100, dense offsets
83, rdr2geo 77, prepare 57, ionosphere 46 plus four sub-band legs, geo2rdr
42, geocode 7, tides 4, baseline 2.

## Two failures worth recording

**Unwrap looks are absolute, not additive.** `phase_unwrap.range_looks` and
`azimuth_looks` are total looks from the RSLC: `unwrap.py` re-runs crossmul
from the SLCs at those looks and `prepare_insar_hdf5` bakes the resulting
RUNW shape into the product skeletons. Reading them as "looks added on top
of crossmul" put a 5296 x 9478 (50 Mpx) raster into a single SNAPHU tile,
which ran 5.5 h without finishing or erroring. The ionosphere chain runs at
the same looks, so its dispersive-filter kernels are in RUNW pixels too.

**One GPU cannot host two InSAR batches.** A ComfyUI server holding
9.8-14.2 GiB of a 16 GiB card caused a pair to die of
`cudaErrorMemoryAllocation` in the ionosphere sub-band resample, after
which every subsequent pair aborted within ~10 s at
`gpuDEMInterpolator.cu:126` (SIGABRT). With `--keep-going` that consumed
five pairs in 40 seconds. The runner now waits on `MIN_FREE_VRAM_MB` before
each pair, which prevents the cascade but cannot survive another process
taking memory mid-run — the three pairs lost this way were simply retried
once the card was free, with the identical runconfig.

## Reproducing

```bash
make data-alos2     # 73 GiB from JAXA's FTP
make alos2-convert  # 12 RSLCs, ~5 min each
make alos2-network  # 66 runconfigs + the closure manifest
make alos2-run      # sequential GPU batch
```

JAXA "Terms for Use" apply to the sample data.
