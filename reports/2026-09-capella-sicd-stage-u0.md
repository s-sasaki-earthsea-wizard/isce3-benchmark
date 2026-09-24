# Capella stripmap SICD through isce3: RGZERO converter, geometry to 0.1 mm, and the phase convention measured

- Date: 2026-09-23/24
- Host: NucBox EVO T1, 16 CPUs, 93 GB RAM, one 16 GiB GPU (`ISCE_CUDA_ARCHS=120`).
- isce3 commit: upstream `develop` `2919e1c97` (`0.26.0-dev+2919e1c97`), from-source
  container build (`isce3-benchmark:dev`, `isce3-build/`).
- isce3-benchmark commit: this branch (`feat/sicd-to-nisar-rslc`).
- Tracking issue: [bench#54](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/54).
- Data: Capella open data, Mexico City stripmap stack, satellite C14, HH,
  `CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055.ntf` (reference) and
  `..._20240629134910_20240629134915.ntf` (secondary); 2 d 22 h 48 m apart.
  SICD 1.3.0 written by "Capella SAR Processor (3.2.1)". DEM: Copernicus GLO-30
  via `fetch/fetch_dem_bbox.py` (bbox −99.25 19.10 −98.75 19.60, ellipsoidal).
- Independent reference: sarkit 1.12.0 (Valkyrie Systems' toolkit for the NGA SAR standards),
  host venv, never sees isce3.
- Evidence: `artifacts/sicd-capella-20260923/` (conversion summaries, both
  halves of the geometry cross-check, fringe-rate summary + quicklook, RIFG
  timings), `artifacts/sicd_headers/` (SICD XML and SLC extended JSON of both
  scenes).

## Summary

isce3 has no SICD reader. For the half of SICD whose image grid already is a
zero-Doppler slant-range grid (`Grid/Type=RGZERO`, `RMA/ImageType=INCA`:
Capella stripmap and sliding spotlight), a repackaging into the NISAR RSLC
layout is enough, and this report checks the result against an independent
implementation and a measured phase test, not merely that it runs:

1. **Converter** (`tools/sicd_to_nisar_rslc.py`): both scenes in 42 s / 1.07 GB
   RSS each; products open with the isce3 readers. RGAZIM/PFA (all Umbra,
   Capella spotlight) is rejected by name.
2. **Geometry** (`tools/sicd_rslc_geometry_check.py`): isce3 on the converted
   RSLC and sarkit on the original SICD XML, reading the same header, put the
   same image locations (52 projection evaluations per scene) on the same two
   constant-height surfaces **0.08 mm / 0.15 mm apart** (scene 1 / 2), i.e.
   1e-4 pixel. This is agreement between implementations, not absolute
   geolocation accuracy. The isce3 round trip closes to 1.6e-4 line /
   5e-9 sample.
3. **Phase convention** (`tools/rifg_fringe_rate.py`): the "Backprojected to
   DEM" processing tag was the one finding that could have sunk InSAR. It does
   not. The raw interferogram's fringe frequency equals the geometric
   prediction isce3 uses for flattening (**0.994× in range, 1.002× in
   azimuth**) and the flattened product's residual is **−0.005× / +0.006×**.
   For this pair that is consistent with the conventional sign the workflow
   assumes (`exp(−j4πR/λ)`, carrier retained); it does not establish a
   universal SICD phase reference or an absolute SLC phase. Single-scene range spectra agree: baseband, not the
   −0.267 cycles/px shift a grid-compensated image would carry.
4. **End to end**: the 3-day pair runs through `insar.py` to RIFG on the GPU in
   **263 s** (5 × 3 looks, 6397 × 2126, mean coherence 0.43, 36 % of pixels
   above 0.5).

Four findings came out that were not in the plan. Two are Capella header
issues (§6), one is an isce3 limitation that blocks any non-L/S-band RSLC
(§5), and one is a methodological trap in measuring fringe rates at moderate
coherence (§4.3).

## 1. Converter

`tools/sicd_to_nisar_rslc.py` mirrors `share/nisar/examples/alos2_to_nisar_l1.py`
so that everything `cxx/isce3/product/Serialization.h` and
`nisar/products/readers` dereference is present. Every grid, orbit and frequency
value comes from the header and nothing is hard-coded to Capella; fields SICD
does not describe (orbit/track/frame numbers, acceleration, angular velocity,
antenna pattern, noise LUT) are placeholders filled the way
`alos2_to_nisar_l1.py` fills them. The product is an adapter for
reader/workflow compatibility, not a NISAR-schema-conformant or
radiometrically calibrated RSLC.

| item | rule | measured on the pair |
|---|---|---|
| gate | `Grid/Type=RGZERO` ∧ `RMA/ImageType=INCA` ∧ `ImagePlane=SLANT`; `TimeCAPoly` order 1 | passes; an edited RGAZIM/PFA header exits 1 naming the grid type |
| NITF | header + image-subheader parsed; one segment, `IC=NC`, `IMODE=P`, `NBPR=NBPC=1`, bands I,Q, `PVTYPE/NBPP` ↔ `PixelType`, length = rows·cols·4 | all satisfied; data at byte 929 |
| pixels | `np.memmap('>i2')`, blocked transpose range-major → azimuth-major, compound float32 `(r,i)`, gzip-1 | 204 Mpx → 554 / 545 MB |
| azimuth flip | `sign(RMA/INCA/TimeCAPoly[1]) < 0` ⇒ line ℓ = column `NumCols−1−ℓ`; start/end and valid rows flip together | flipped; `uCol·v̂ = −1.000000` |
| epoch | midnight UTC of `CollectStart` (parsed to ns) | `2024-06-26T00:00:00` |
| orbit | `Position/ARPPoly` every 0.1 s over [min(t_img, 0)−1 s, max(t_img, CollectDuration)+1 s], analytic velocity | 66 SV; speed 7215.49–7215.52 m/s, radius 6974.3 km |
| attitude | `Antenna/Tx/{X,Y}AxisPoly` → Gram-Schmidt → `isce3.core.Quaternion(rotation_matrix)`, TCN fallback | max X·Y 2e-9 |
| native Doppler | zero LUT (RGZERO is zero-Doppler by definition) | `DopplerConeAng` 90.000000375° |
| valid samples | first/last non-zero I/Q per line, not `ImageData/ValidData` | last valid sample 10605–10624 / 10581–10597 |
| bounding polygon | `get_geo_perimeter_wkt` on a constant surface at the SCP HAE | 2245 m |
| provenance | verbatim SICD XML + SHA-256, NITF layout, flip flag, diagnostics JSON under `metadata/processingInformation/inputs` | used by the geometry checker |

Runtime: 42.4 s / 43.1 s wall, 1.07 GB RSS (`/usr/bin/time -v`), of which the
transpose + gzip is ~36 s. The reader-side check (`nisar.products.readers.SLC`,
`RadarGridParameters`, orbit, attitude, Doppler LUT, dataset as complex64)
passes on `develop`; the only reader remark is the `hasInputDataException`
info-level field, which the converter now writes.

## 2. Geometry: two implementations, one header

Acceptance 2 asked for a self round-trip **and** an independent mapping,
because a round trip proves consistency, not correctness.

Point set: 5 × 5 grid over the SICD (row, col) space plus the SCP pixel, on
HAE_SCP = 2244.94 m and HAE_SCP + 500 m: 52 projection evaluations; the SCP
coincides with the grid centre, so 50 unique location/height combinations. Both halves read the SICD
XML embedded in the RSLC, so they see the same header bytes.

| | scene 1 (06-26) | scene 2 (06-29) |
|---|---|---|
| isce3 `rdr2geo_bracket` vs sarkit `image_to_constant_hae_surface`, max ‖d‖ | **0.08 mm** | **0.15 mm** |
| same, in range pixels (0.617 m) | 1.3e-4 | 2.4e-4 |
| isce3 round trip, max \|Δline\| / \|Δsample\| | 1.4e-4 / 4.5e-9 | 1.6e-4 / 5.3e-9 |
| `rdr2geo`(SCP pixel) − `GeoData/SCP/ECF` | 4.98 mm | 2.06 mm |
| `geo2rdr`(SCP ECF) − SCP pixel (lines, samples) | (−0.0046, +0.00018) | (−0.0018, +0.00003) |
| sarkit `scene_to_image`(SCP ECF), (xrow, ycol) | (0.11 mm, **4.98 mm**) | (0.02 mm, **2.06 mm**) |

The 1.4e-4-line round-trip residual is `geo2rdr_bracket`'s default azimuth-time
tolerance (2.2e-8 s), not geometry.

The SCP rows are the interesting ones: isce3 puts the SCP pixel 5 mm from the
header's SCP ECF, and **sarkit puts the header's SCP ECF the same 5 mm from the
SCP pixel**, in the same (along-track) direction. Two independent
implementations agree on the offset, which points to a millimetre-level
inconsistency between `GeoData/SCP/ECF` and the polynomial geometry in the
header (not investigated further). Irrelevant for InSAR; not something a
converter should "fix".

## 3. End to end: `insar.py` to RIFG

`configs/insar_capella_mexico_city_template.yaml` (from the ALOS-2 template),
RIFG only, looks 5 × 3 derived from the 0.617 m × 1.0625 m header spacings
(3.09 m slant / 4.71 m ground × 3.19 m), ionosphere off, dense offsets +
rubbersheet + fine resample on, GPU on, scratch kept.
`scripts/run_capella_pair.sh flat noflat` runs the two crossmul-flatten
variants on identical inputs.

| step (GPU where available) | flatten = true | flatten = false |
|---|---|---|
| rdr2geo | 30.7 s | 30.8 s |
| geo2rdr | 8.2 s | 7.8 s |
| prepare_insar_hdf5 | 66.5 s | 75.3 s |
| coarse resample | 18.6 s | 18.1 s |
| dense offsets | 12.7 s | 11.2 s |
| rubbersheet (polyfit) | 63.3 s | 58.4 s |
| fine resample | 37.6 s | 37.7 s |
| crossmul | 15.3 s | 16.5 s |
| **INSAR total** | **256.8 s** (wall 263 s, RSS 10.3 GB) | **261.3 s** (276 s, 10.1 GB) |

Interferogram: 6397 × 2126; coherence mean 0.433 (flat) / 0.432 (noflat),
36.1 % / 35.8 % of pixels above 0.5. Dense offsets after geometric
coregistration: median **+0.198 px slant range, +1.94 px along track** (p5–p95:
+0.15..+0.21, +1.73..+2.15), correlation peak 0.75 — a 2 m along-track offset
between the two dates after orbit-based coregistration, which rubbersheeting
absorbs (§6.3).
`perpendicularBaseline` from the product's geolocation grid: 622.5 m.

## 4. Phase convention: measured, not argued

### 4.1 Why it mattered

Both headers carry `ImageFormAlgo=OTHER` with
`Processing/Type = "Backprojected to DEM"`. Time-domain backprojection natively
references each pixel's phase to the range of the grid point being formed
(Duersch 2013/2015); if Capella delivered that, the topographic + flat-earth
phase would already be gone and isce3 would remove it a second time. Capella's
documentation never states the phase reference (the desk survey is in
`.claude-notes/2026-09-23-capella-phase-convention-research.md`). The SLC
extended JSON of the same collects adds that the "DEM" is
`ExplicitInflatedWGS84[2234.00]` — a constant-height ellipsoid, not terrain.

### 4.2 The test

`tools/rifg_fringe_rate.py`. crossmul forms `ref · conj(sec)` and, when
flattening, multiplies by `exp(−j·4π·dr·off/λ)` with `off` =
`geo2rdr/freqA/range.off` (`cxx/isce3/signal/Crossmul.cpp`). Let f_geo be the
local fringe frequency of that geometric phase on the RIFG grid, f_raw the
measured one on the flatten-off product, f_flat on the flatten-on one:

| convention | f_raw | f_flat |
|---|---|---|
| conventional, `exp(−j4πR/λ)` | +f_geo | 0 |
| opposite sign | −f_geo | −2 f_geo |
| compensated to the focusing surface | 0 | −f_geo |

Measured, medians over 64-row × 512-column blocks (range) and 512 × 64
(azimuth) with block coherence ≥ 0.3 (392/396 and 352/396 blocks):

| | range [cycles/RIFG px] | azimuth [cycles/RIFG px] |
|---|---|---|
| f_geo | −0.1857 | −0.0321 |
| f_raw | **−0.1849** | **−0.0322** |
| f_flat | **+0.0010** | **−0.0002** |
| f_raw / f_geo | **0.994** | **1.002** |
| f_flat / f_geo | −0.005 | +0.006 |
| spectral peak / median power, raw → flat | 12.9 → 39.7 | 16.8 → 43.8 |

f_geo in range is 0.0371 cycles per full-resolution pixel = one fringe every
26.9 slant pixels (16.6 m slant, 25 m ground), consistent with the flat-earth rate for
B⊥ = 622 m at 764.6 km and 40.9° incidence. The raw interferogram carries it;
the flattened one does not. **Consistent with the conventional sign isce3's
flattening assumes** (for this pair; see §7 for what this does not establish).

Single-scene corroboration measured during conversion (`Grid/Row`): the
range spectrum of the pixels is at baseband (centre +0.0000 cycles/px, width
0.816 vs header 0.824). A phase-compensated-to-grid image would be shifted by
`frac(KCtr·SS)` = −0.267 cycles/px. It is not.

### 4.3 A trap: the naive fringe-rate estimator is biased at this coherence

The first version of the test estimated the local fringe frequency as
`angle(Σ W[i+1]·conj W[i]) / 2π` and returned f_raw = 0.43 f_geo,
f_flat = −0.30 f_geo — nonsense for any convention. The block-averaged power
spectrum of the same blocks showed a clean line at 0.99 f_geo (raw) and at 0
(flat). The mechanism: speckle correlation between adjacent multilooked pixels
contributes a zero-frequency term to the phasor-difference sum that is
comparable to the coherent term at γ ≈ 0.44; it pulls the circular mean
towards 0 on the raw product, and — because flattening shifts that hump by
+f_geo — towards +f_geo on the flattened one. Both measured biases match.
`np.unwrap`-and-fit along a row fails the same way (−0.034 vs −0.186). The
spectral peak separates the fringe line from the hump. Anyone reproducing this
test should use the spectral estimator.

## 5. isce3 finding: the InSAR product writer refuses X-band

First RIFG attempt died after rdr2geo (7.9 s, 204,019,521 / 204,019,521
converged) and geo2rdr had completed:

```
File ".../nisar/products/insar/InSAR_base_writer.py", line 1299, in _get_band_name
    raise ValueError("Unknown frequency encountered. Not L or S band")
```

`InSARBaseWriter._get_band_name` maps `processedCenterFrequency` to "L" or
"S" and raises otherwise, although the letter only feeds two identification
strings (`instrumentName` = "<band>-SAR", `radarBand`). `granule_id.get_radar_band`
in the same package handles the case gracefully (returns "A" with a warning).
Bench-side workaround: `scripts/run_insar_xband.py` replaces the method with
the IEEE band table (L S C X Ku K Ka) and runs the stock entry point; the
isce3 tree is untouched. **Candidate for the upstream RFC**: every step of the
InSAR chain is band-agnostic except this check.

## 6. Capella header findings (not isce3)

### 6.1 SCP ECF vs polynomial geometry: 5.0 mm / 2.1 mm along track

§2. Two implementations agree on the offset. Not an InSAR problem.

### 6.2 `Grid/Col` does not describe the azimuth band in the data

Measured on the raw pixels (192 rows averaged), SICD column direction:

| | scene 1 | scene 2 | header |
|---|---|---|---|
| processed-band centre (−6 dB notch edges) | +0.0723 cpp = **+449 Hz** | +0.0674 cpp = **+419 Hz** | `DeltaKCOAPoly·SS` = −0.0036 cpp = −22.5 / −20.7 Hz |
| processed-band width (−6 dB) | 0.882 cpp = 5477 Hz | 0.884 cpp = 5491 Hz | `ImpRespBW·SS` = 0.809 cpp = 5023 Hz |
| notch depth | 32 dB | 32 dB | |

`ImageBeamComp = NO`, `STBeamComp = NO`, weighting `ANTENNA-TAPER-CAPELLA`, so
the taper on the data is the actual two-way pattern. The offset is common to
both dates (Δ 30 Hz, 0.5 % of the band), so it should not by itself cost
coherence on this pair — not tested by a controlled comparison; `processedAzimuthBandwidth` in the RSLC is the header's 5023 Hz.
Range is clean (width 0.816 vs 0.824 cpp, centre 0). GAMMA reported
"Doppler Centroid values between −452 Hz and −266 Hz" on the TIFF SLCs of the
same stack (`2025-1_TR_Capella_PSI_Mexico.pdf`), which is the same order.

### 6.3 A 2 m along-track offset between the two dates

Dense offsets after orbit-based coregistration are +1.94 px (2.06 m) along
track and +0.20 px (0.12 m) in range, uniform across the scene (p5–p95 spread
0.4 px). Each header's geometry agrees between two implementations to 0.1 mm
(§2), so one possible explanation is a between-date inconsistency of the
producer's timing/orbit annotations (2.06 m ≈ 0.3 ms of along-track motion);
the cause was not investigated.
Standard rubbersheeting removes it; a geometry-only coregistration would not.

## 7. What this does and does not establish

- Established: a restricted class of RGZERO/INCA SICD (the rules in §1)
  becomes an isce3-readable RSLC after a repackaging, with geometry agreeing
  with an independent implementation (sarkit) to 0.1 mm on the same header,
  and the dominant fringe rates of a real pair consistent with the flattening
  sign the workflow assumes. For this pair and RIFG configuration, the only
  isce3 code change needed was the band-name check override; RUNW/GUNW and
  other bands were not tested.
- Not established: deformation. The 3-day pair carries ≤ 0.2 fringe of
  plausible LOS motion (λ/2 = 15.5 mm), so the flattened residual (atmosphere,
  DEM error at h_amb 12.4 m) is not a deformation map and was not treated as
  one.
- Not attempted: RUNW/GUNW (unwrapping 13.6 Mpx in one SNAPHU tile is a
  separate budget), the long pairs, the PFA/RGAZIM half.

## 8. Reproduce

```
fetch/fetch_capella_mexico_city.sh                       # 1.6 GB, anonymous HTTPS
docker compose run --rm dev python fetch/fetch_dem_bbox.py --bbox -99.25 19.10 -98.75 19.60 \
    --out /data/capella_mexico_city/dem.tif
docker compose run --rm dev python tools/sicd_to_nisar_rslc.py \
    /data/capella_mexico_city/CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055.ntf \
    /data/capella_mexico_city/rslc/20240626.h5                       # and 20240629
docker compose run --rm dev python tools/sicd_rslc_geometry_check.py isce3 \
    /data/capella_mexico_city/rslc/20240626.h5 --out /data/capella_mexico_city/rslc/geom_isce3_20240626.json
python -m venv venv-sarkit && venv-sarkit/bin/pip install sarkit h5py   # host
venv-sarkit/bin/python tools/sicd_rslc_geometry_check.py sarkit data/capella_mexico_city/rslc/20240626.h5 \
    --out data/capella_mexico_city/rslc/geom_sarkit_20240626.json
python tools/sicd_rslc_geometry_check.py compare data/.../geom_isce3_20240626.json data/.../geom_sarkit_20240626.json
scripts/run_capella_pair.sh flat noflat
docker compose run --rm -v $HOME/scratch/capella:/scratch_root dev python tools/rifg_fringe_rate.py \
    --flat /data/capella_mexico_city/rifg/rifg_capella_mexico_city_20240626_20240629_flat/product.h5 \
    --noflat /data/capella_mexico_city/rifg/rifg_capella_mexico_city_20240626_20240629_noflat/product.h5 \
    --range-off /scratch_root/rifg_capella_mexico_city_20240626_20240629_flat/geo2rdr/freqA/range.off \
    --rslc /data/capella_mexico_city/rslc/20240626.h5 --out fringe_rate_summary.json --png quicklook.png
```
