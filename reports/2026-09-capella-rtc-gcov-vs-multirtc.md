# Capella SICD through isce3 GCOV, cross-checked against MultiRTC

- Date: 2026-09-24
- Host: NucBox EVO T1, 16 CPUs, 93 GB RAM (GCOV and MultiRTC run on CPU).
- isce3: upstream `develop` `2919e1c97` (`0.26.0-dev+2919e1c97`), from-source
  container build — **both** paths below import this build.
- isce3-benchmark: this branch (`feat/sicd-rtc-gcov`, stacked on
  `feat/sicd-to-nisar-rslc` / PR #56).
- Comparator: MultiRTC v0.5.4, commit `a0edba80a05c923b03ffae378e4a1faf293b0f0f`
  (BSD-3), used read-only (installed `--no-deps` into `data/external/`), with
  sarpy 1.3.59.
- Data: Capella open data, `CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055`
  (Mexico City, stripmap, left-looking, 19191 × 10631). DEM: Copernicus GLO-30
  (`fetch/fetch_dem_bbox.py`, ellipsoidal) — the same file for both paths.
- Evidence: `artifacts/sicd-capella-rtc-20260924/`.
- Follows `reports/2026-09-capella-sicd-stage-u0.md` (converter, geometry,
  InSAR phase). Tracking: bench#54.

## Summary

The SICD → NISAR RSLC converter now has a `--radiometry beta0` option, and the
beta0 product runs through isce3's own RTC workflow (`nisar.workflows.gcov`)
on X-band, metadata included, in 3 min 51 s.

To test that path against an existing downstream implementation, the
same SICD was run through MultiRTC with matched options on the same isce3
build and DEM. Both paths share one geocode/RTC core, so this compares the
**ingest and packaging**, not RTC algorithms, and it says nothing about InSAR
phase.

| MultiRTC variant | map offset vs GCOV, predicted by rdr2geo (along / near-range) | measured by tile correlation | gamma0 within 0.01 dB |
|---|---|---|---|
| stock | −0.91 m / 9.67 m | −1.17 m / 8.84 m | 0.2 % |
| matched (shadow masking off) | −0.91 m / 9.67 m | −1.17 m / 8.84 m | 0.2 % |
| matched + start-time fix | +0.16 m / 9.65 m | +0.15 m / 8.58 m | 0.2 % |
| matched + start-time fix + starting-range fix | −0.006 m / 0.000 m | 0.000 m / 0.000 m | **99.98 %** |

The last column is over the common support. The wavelength difference (§3.1) is
**not** corrected in any variant; it does not enter RTC but would enter any phase use.

With two corrections to MultiRTC's radar grid — applied as runtime
subclasses, its source untouched — the two paths agree closely: over
8,097,578 common valid pixels, 99.9804 % differ by less than 0.01 dB
(p5/p95: −0.001392 / +0.001389 dB); 5,764 edge pixels are valid in MultiRTC
only. The current (biased, §3.2) correlation estimator reports a median
along-track shift of 0.206 mm across 99 tiles; this is not an established
sub-millimetre accuracy bound. Without the corrections, MultiRTC's radar grid
places pixels one azimuth line early and ~9.7 m towards near range (rdr2geo,
§3.2).

The calibrated radar-domain pixels agree before any geocoding: MultiRTC's
complex beta0 raster and the beta0 RSLC image match to 1.2e-7 relative and
6.0e-8 rad over 203,810,191 pixels, with an identical zero pattern.

## 1. The beta0 option

GCOV expects a beta0-calibrated RSLC: it reads
`calibrationInformation/geometry/beta0` (and treats it as all ones) while
writing its metadata, and subtracts the noise LUT in the same radiometry as
|pixel|². The DN product has neither, so the RSLC reader raised `KeyError`.

`--radiometry beta0` (default stays `dn`):

- each pixel × sqrt(`Radiometric/BetaZeroSFPoly`(xrow, ycol)), evaluated at its
  own SICD coordinates through the same flip / transpose / block path as the
  pixels — a real factor, so the phase is untouched;
- noise LUT in beta0 power; `geometry/beta0` = 1; non-finite or non-positive
  factors rejected; provenance string in `processingInformation/inputs`.

In both modes the noise LUT now evaluates the full `NoisePoly`, **clamped to
the image extent**: the LUT grid is padded 20 km in range, where the quadratic
Capella polynomial extrapolates to +190 dB.

| check | result |
|---|---|
| unit tests (`tests/test_sicd_radiometry.py`) | 13 passed: non-constant synthetic polynomial (~100 % variation), flip on/off, blocks of 1/7/64 lines, phase, power ratio, rejection, LUT/image coordinate mapping for both time-slope signs, edge clamping |
| DN mode vs the RSLC the RIFG runs used | 82 datasets identical incl. the image; noise LUT, diagnostics JSON, processingDateTime differ by design |
| beta0 vs DN, 4 Mpx window | power ratio 2.416260e-05 = `BetaZeroSFPoly[0,0]` to float32 rounding; max phase change 5.6e-08 rad |
| noise LUT, beta0 power | −24.1 … −18.6 dB (scene 2: −23.6 … −16.9 dB); DN²: 22.1 … 27.6 dB (was a constant 22.19 dB) |

## 2. isce3 GCOV on X-band

`configs/gcov_capella_mexico_city_20240626.yaml`: input beta0, output gamma0,
area-projection geocode and RTC, `rtc_min_value_db` −30, `dem_upsampling` 2,
biquintic DEM, geo2rdr 1e-7 / 50, `abs_rad_cal` 1, no noise correction,
single-block memory, 5 m in EPSG:32614 — MultiRTC's defaults for a SICD except
shadow masking.

It ran through with warnings only for absent optional metadata (crosstalk,
`inputDataExceptionMask`, `productDoi`, …): 3 min 51 s wall, 5.9 GB RSS (RTC
158 s inside a 221 s geocode). gamma0 median −9.28 dB (p5/p95 −15.6 / +0.9 dB);
geocoded noise −25.9 … −18.7 dB. `identification/radarBand` = X is carried over
from the RSLC. Unlike the InSAR writer, nothing in the GCOV path rejects
X-band. (Its granule-ID code expects 20/40/77/5 MHz bandwidths, but only runs
when a partial granule ID is configured.)

## 3. MultiRTC as a diagnostic comparator

MultiRTC maps an RGZERO SICD onto in-memory isce3 objects (`SicdRzdSlc`) and
calls the same isce3 geocode/RTC functions. It was run with its own defaults
(`stock`) and three controlled variants (`tools/multirtc_capella_rtc.py`).
Stock v0.5.4 is a diagnostic comparator here, not a reference.

### 3.1 Radar grid differences, stock

| quantity | MultiRTC − ours |
|---|---|
| sensing start | −1.006 lines (−1.619e-4 s) |
| starting range | −6.323 m (−10.24 samples) |
| PRF | ratio 1.000000 |
| wavelength | 2.078 cm vs 3.107 cm |
| orbit positions at common times | ≤ 7.3 mm |

Three causes, each read from the pinned source (`src/multirtc/sicd.py`):

1. **Start time.** The "last column" time is evaluated at index N, one past
   the image. For a time-reversed azimuth axis — as on these inputs, whose
   `TimeCAPoly` slope is negative (MultiRTC branches on its own `az_reversed`
   flag) — that value becomes `sensing_start`: one line early. The PRF stays
   right.
2. **Starting range.** `get_starting_range(0)` measures from the ARP at closest
   approach to a point on the plane tangent at the SCP. That equals the RGZERO
   row-0 range `R_CA_SCP − SCPPixel.Row × Row.SS` only at the SCP column:

   | SICD column (ycol) | MultiRTC − RGZERO row-0 range |
   |---|---|
   | 0 (−10.2 km) | −6.323 m |
   | 4797 (−5.1 km) | −1.581 m |
   | 9595 (SCP) | +0.000 m |
   | 14393 (+5.1 km) | −1.581 m |
   | 19190 (+10.2 km) | −6.324 m |

   Quadratic in the along-track distance, and the column-0 value is applied to
   the whole grid.
3. **Wavelength.** `TxFrequency.Min + TxFrequency.Max / 2` (precedence) gives
   14.425 GHz instead of 9.650 GHz. Zero-Doppler geometry and RTC do not use
   the wavelength, so it does not appear in the table above; it would matter
   for any phase use.

With both geometry fixes, the residual start difference on this scene is
0.93 µs, equal to the nanoseconds of `Timeline/CollectStart` (`…51.301024927`)
that MultiRTC's microsecond datetime drops.

### 3.2 Geocoded comparison

Support on the shared grid (5817 × 5829 overlap): with both fixes 8,097,578
common pixels, 0 GCOV-only, 5,764 MultiRTC-only (edges). Shadow masking changes
the support by ~100 pixels and nothing else.

Map offsets are reported two ways:

- **predicted**: rdr2geo of 25 pixels through both radar grids with *our*
  orbit and a constant height of 2245 m — a grid-only control, not the exact
  offset of the two outputs (each path also has its own orbit sampling and
  epoch). For the fully fixed variant its −6 mm along-track is the 0.93 µs
  epoch difference evaluated on one orbit; with each path's own orbit the same
  25 points differ by +0.54 mm along track (max ECEF difference 0.54 mm;
  cross-check in the team review);
- **measured**: phase correlation of 256 × 256 dB tiles (99 tiles).

The correlation estimator (Hann window, parabolic peak) is **biased**, in
both directions: on GCOV tiles shifted by a known amount it recovered
0.2126 px as 0.117 px and 1.40 px as 1.30 px, but 1.9334 px as 1.966 px
(`estimator_bias.txt`). The measured column is therefore a biased estimate,
not a bound. The prediction gives the size of the grid-induced offset, and
the intervention (last row of the summary table) gives the attribution:
removing the two grid differences removes the offset to within what this
estimator can resolve, and the radiometric agreement above follows.

`quicklook_20240626.png`: GCOV gamma0, and MultiRTC − GCOV for the matched and
the fully fixed variant.

## 4. What this does and does not establish

- Established: the beta0 RSLC is accepted by isce3's GCOV workflow on X-band,
  and on this scene its RTC output agrees with that of an independent ingest
  path (MultiRTC's) to the level above once two identified grid differences
  are removed. Together with the earlier report, the same converter feeds
  isce3's InSAR workflow (DN mode) and its RTC workflow (beta0 mode).
- Not established: RTC accuracy against ground truth (both paths share the
  isce3 RTC core); anything about phase (RTC is power); other scenes or
  producers. Scene 2 was converted to beta0 but not run through GCOV.
- The MultiRTC findings are stated for the pinned v0.5.4 on this input. The
  repository has had no merge since 2026-03-07, and nothing has been reported
  upstream.

## 5. Reproduce

```
make capella-convert-beta0     # beta0 RSLCs
make capella-gcov              # isce3 GCOV on 2024-06-26
make multirtc-setup            # pinned MultiRTC + sarpy into data/external (read-only)
make capella-multirtc          # four variants
make capella-rtc-compare       # comparisons -> data/capella_mexico_city/rtc_compare/
docker compose run --rm dev python -m pytest -q tests/test_sicd_radiometry.py
```
