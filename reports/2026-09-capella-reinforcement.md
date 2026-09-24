# Capella reinforcement: the SICD path on all four look / pass corners

- Date: 2026-09-24
- Host: NucBox EVO T1, 16 CPUs, 93 GB RAM, one 16 GiB GPU.
- isce3: upstream `develop` `2919e1c97` (`0.26.0-dev+2919e1c97`), from-source container build.
- isce3-benchmark: branch `feat/capella-reinforcement` (stacked on `feat/sicd-rtc-gcov-compare`, bench PR #59).
- Tracking issue: [bench#54](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/54).
- Follows `reports/2026-09-capella-sicd-stage-u0.md` (converter, geometry, phase convention on one
  pair) and `reports/2026-09-capella-rtc-gcov-vs-multirtc.md` (beta0, GCOV, MultiRTC on one scene).
- Independent reference: sarkit 1.12.0, host venv, never sees isce3.
- Evidence: `artifacts/sicd-capella-reinforcement-20260924/`, headers in
  `artifacts/sicd_headers/reinforcement/`.

## Summary

The Stage U0 evidence came from one left-looking ascending pair. This report repeats every check
on five more Capella stripmap scenes, so that each look side and pass direction is covered, with
the same converter, templates and isce3 build.

1. **Coverage.** L-A Mexico City pair (earlier), R-A and L-D Niscemi pairs, R-D Yumare single scene.
   The Capella open-data InSAR set has no R-D repeat pair, so R-D is covered for conversion,
   geometry and GCOV only.
2. **Geometry.** isce3 on the converted RSLCs and sarkit on the original headers differ by up to
   **3.4 cm** (0.055 px) on the stock sarkit projection. The difference tracks the header's
   centre-of-aperture (COA) time offset from closest approach, which is 0 on the Mexico City
   scenes and up to 28.5 ms here. With `TimeCOAPoly` set equal to `TimeCAPoly`, the two agree
   to **≤ 0.04 mm on all five scenes**. So the converter reproduces the header's closest-approach
   (INCA) definition exactly; the centimetres sit between the SICD's own COA and closest-approach
   descriptions.
3. **Phase convention.** Both new pairs give the conventional sign, `exp(−j4πR/λ)`, as the
   Mexico City pair did: range fringe rate raw/geometric 0.959 (R-A) and 1.005 (L-D), flattened
   residual −0.017 and 0.00003 of the geometric rate.
4. **New isce3 finding.** isce3 bandpasses a secondary SLC whose header centre frequency is 26 Hz
   off (2.7e-9 relative), and the bandpass then fails its integer-ratio check on float rounding
   for one of the two pairs. An isce3-only reproducer is included.
5. **GCOV.** isce3's RTC workflow runs on all five beta0 RSLCs (2.5-3.5 min each, `radarBand = 'X'`).
   On the right-looking reference scene, MultiRTC's start time is right and only its starting
   range differs (−7.23 m, the tangent-plane error); with that one fix, **99.80 %** of 4.5 M common
   pixels agree within 0.01 dB (Mexico City, left-looking: 99.98 % after two fixes).

## 1. Scenes

| corner | site | scenes (start, UTC) | satellite | size (az × rg) | B_perp (isce3) |
|---|---|---|---|---|---|
| L-A | Mexico City | 2024-06-26 15:00, 2024-06-29 13:49 | C14 | 19191 × 10631 | 606-639 m |
| R-A | Niscemi, Italy | 2026-02-04 11:45, 2026-02-07 10:41 | C13 | 20489 × 6684 | 155-156 m |
| L-D | Niscemi, Italy | 2026-02-04 18:32, 2026-02-07 17:29 | C13 | 19910 × 4832 | 252-254 m |
| R-D | Yumare, Venezuela | 2026-06-27 14:41 (single) | C15 | 23254 × 11408 | — |

All are SICD 1.3.0, `Grid/Type=RGZERO`, `RMA/ImageType=INCA`, 200 MHz, 0.617 m slant sampling.
Right-looking scenes have a positive `TimeCAPoly` slope (no azimuth flip); left-looking ones
negative (flip), as in Stage U0. B_perp is isce3's `perpendicularBaseline` cube range from the RIFG
products. Download: `fetch/fetch_capella_sicd.sh --set reinforcement` (2.9 GB).

## 2. Conversion

`scripts/capella_reinforcement_steps.sh convert` / `convert-beta0` (`artifacts/.../conversion_summary.json`):

| scene | DN | beta0 |
|---|---|---|
| 20260204114511 (R-A ref) | 30.3 s | 42.4 s |
| 20260207104155 (R-A sec) | 23.4 s | 39.0 s |
| 20260204183253 (L-D ref) | 21.4 s | 37.4 s |
| 20260207172937 (L-D sec) | 17.3 s | 34.6 s |
| 20260627144157 (R-D) | 40.3 s | 83.5 s |

## 3. Geometry: two implementations, one header

Same test as Stage U0 §2: 52 projection evaluations per scene (5 × 5 grid plus the SCP pixel, on
two constant-HAE surfaces), isce3 `rdr2geo` on the RSLC vs sarkit
`image_to_constant_hae_surface` on the SICD XML stored in the RSLC.

| scene | max \|t_COA − t_CA\| in header | isce3 vs sarkit, stock: max / median | with t_COA := t_CA: max |
|---|---|---|---|
| Mexico City 0626 / 0629 (Stage U0) | 0 ms / 0 ms | 0.08 mm / 0.15 mm (max) | — |
| Niscemi R-A 0204 | 28.5 ms | 33.7 mm / 1.8 mm | 0.03 mm |
| Niscemi R-A 0207 | 20.6 ms | 24.8 mm / 1.5 mm | 0.01 mm |
| Niscemi L-D 0204 | 6.7 ms | 6.7 mm / 1.4 mm | 0.04 mm |
| Niscemi L-D 0207 | 5.3 ms | 4.9 mm / 0.7 mm | 0.02 mm |
| Yumare R-D 0627 | 1.6 ms | 1.7 mm / 0.2 mm | 0.03 mm |

The stock differences are horizontal (vertical ≤ 0.08 mm). Along a scene they follow the header's
own `t_COA − t_CA`, which is asymmetric: on R-A 0207 near range at 870 m HAE, for example,
1.7 mm at column 0 (−1.2 ms) and 24.8 mm at the last column (−20.5 ms). The geometry tool's
1 cm tolerance fails on the two R-A scenes.

Mechanism, closed by intervention: for INCA the converter maps a column to zero-Doppler time
through `RMA/INCA/TimeCAPoly`, and isce3 projects on that zero-Doppler grid. sarkit projects each
pixel at its COA time (`Grid/TimeCOAPoly`) through the INCA R/Rdot model (`DRateSFPoly`).
`tools/sicd_coa_to_ca.py` rewrites the header with `TimeCOAPoly(x, y) := TimeCAPoly(y)`; sarkit on
that header agrees with isce3 to ≤ 0.04 mm everywhere (last column). The converter therefore
reproduces the header's closest-approach definition exactly, and the stock difference is between
the header's two descriptions of the same pixel. This report does not say which description is
closer to the ground truth; both paths agree to better than 0.06 px.

The isce3 round trip closes to ≤ 1.9e-4 line and ≤ 6e-9 sample on every scene. As on Mexico City
(Stage U0 §6.1), the header's `GeoData/SCP/ECF` is off the polynomial geometry along track, by 5.3,
5.7, 12.2, 32.3 and 19.4 mm (scene order above); sarkit's `scene_to_image` of the same point shows
the same offsets in `ycol`, so it is a header property.

## 4. Phase convention on two more pairs

Same test as Stage U0 §4: RIFG with crossmul flattening on and off on the same registered SLCs
(looks 5 × 3), fringe frequency by block spectral peak (`tools/rifg_fringe_rate.py`), compared with
the geometric prediction from isce3's own `geo2rdr` range offsets. Conventional sign predicts
raw/geo = +1 and flat/geo = 0; the opposite sign −1 and −2; DEM-compensated phase 0 and −1.

| pair | coherence (mean) | range raw/geo | range flat/geo | azimuth raw/geo | azimuth flat/geo | verdict |
|---|---|---|---|---|---|---|
| Mexico City L-A (Stage U0) | 0.43 | 0.994 | −0.005 | 1.002 | +0.006 | conventional |
| Niscemi R-A | 0.35 | 0.959 | −0.017 | 1.086 | +0.084 | conventional |
| Niscemi L-D | 0.43 | 1.005 | 0.00003 | 0.872 | 0.0012 | conventional |

The azimuth geometric rates are small (−0.0088 and 0.0058 cycles per pixel against 0.022 and 0.095
in range), so their ratios scatter more. RIFG shapes 6829 × 1336 (R-A) and 6636 × 966 (L-D);
wall time 155-170 s and 118-122 s per run on the GPU. Quicklooks: `fringe_quicklook_niscemi_{ra,ld}.png`.

## 5. isce3 finding: bandpass on a few-Hz band difference

The first L-D run stopped in `bandpass_insar` after 10 s; the R-A pair had completed, but with its
secondary SLC bandpassed. The Mexico City pair was never bandpassed: its two header frequencies are
identical.

- **Trigger.** `isce3.splitspectrum.splitspectrum.check_range_bandwidth_overlap` compares the two
  wavelengths and bandwidths with `!=`. The Niscemi scenes' header centre frequencies differ by 26 Hz
  at 9.6 GHz (2.7e-9 relative) and their bandwidths by 0.54 Hz, which is enough to bandpass the
  secondary with a Tukey window to a band 0.5 Hz narrower.
- **Failure.** `bandpass_shift_spectrum` forms the new bandwidth as `(fc + bw/2) - (fc - bw/2)` at
  fc ≈ 9.6e9 Hz, where one ulp is 1.9e-6 Hz, and then requires
  `rg_sample_freq % new_rg_sample_freq <= 1e-7` (Hz, absolute). The R-A pair passes by rounding
  (remainder 3.0e-8); the L-D pair fails with "Resampling scaling factor 1.0000000000000095 must be
  an integer." (remainder 2.3e-6). The look side plays no role.
- **Reproducer.** `scripts/repro_bandpass_ratio_check.py`, isce3 only, from the pairs' metadata
  values and an 8 × 1024 random block: `artifacts/.../repro_bandpass_ratio_check.txt` (REPRODUCED).
- **Workaround in this bench.** `scripts/run_insar_xband.py` honours `XBAND_BANDPASS_REL_TOL`
  (off by default), which replaces the overlap check with a relative tolerance and logs the
  decision; the Niscemi pairs run with 1e-6, so all three pairs are processed without bandpass.
- **Effect of the bandpass on this test:** none measurable. The stock R-A run (with bandpass) gives
  the same fringe ratios to four digits (`fringe_rate_niscemi_ra_stockbp.json`).

## 6. GCOV on five more scenes

`scripts/capella_reinforcement_steps.sh gcov` with `configs/gcov_capella_template.yaml` (the Mexico
City settings: beta0 in, gamma0 out, area projection, 5 m posting in the local UTM zone,
single-block memory). Rendered configs and timings are in the artifacts.

| scene | grid (5 m) | valid pixels | gamma0 median (p5 / p95) | wall | max RSS |
|---|---|---|---|---|---|
| 20260204114511 (R-A) | 4377 × 4975, EPSG:32633 | 4.51 M | −8.3 dB (−11.2 / −5.4) | 2:38 | 4.1 GB |
| 20260207104155 (R-A) | 4367 × 4962, EPSG:32633 | 4.50 M | −8.8 dB (−11.9 / −5.8) | 2:27 | 4.0 GB |
| 20260204183253 (L-D) | 5534 × 5498, EPSG:32633 | 4.42 M | −10.2 dB (−14.4 / −6.6) | 2:46 | 3.0 GB |
| 20260207172937 (L-D) | 5537 × 5500, EPSG:32633 | 4.42 M | −9.7 dB (−13.9 / −6.2) | 2:42 | 3.0 GB |
| 20260627144157 (R-D) | 4184 × 4237, EPSG:32619 | 6.89 M | −9.6 dB (−12.1 / −6.7) | 3:21 | 7.6 GB |

The valid fraction of each grid (15-21 %, 39 % for Yumare) is the area of the rotated strip over
its bounding box.

### 6.1 MultiRTC on the right-looking scene

The Mexico City comparison was left-looking, where MultiRTC v0.5.4 has two grid errors (start time
one line early, starting range from the SCP tangent plane). On a right-looking scene only the
second should remain. Same tools as the RTC report (`tools/multirtc_capella_rtc.py`,
`tools/compare_rtc.py`), on 20260204114511:

| MultiRTC variant | start − ours | starting range − ours | predicted offset (along / across) | common pixels | within 0.01 dB | within 0.1 dB |
|---|---|---|---|---|---|---|
| stock | −0.005 line | −7.23 m (−11.7 samples) | +0.49 m / −8.79 m | 4,504,457 | 0.51 % | 5.1 % |
| matched (shadow masking off) | −0.005 line | −7.23 m | +0.49 m / −8.79 m | 4,504,819 | 0.51 % | 5.1 % |
| matched + starting-range fix | −0.005 line | 0 | −0.006 m / −0.0003 m | 4,510,602 | **99.80 %** | 99.9994 % |

- The start time is right on this scene, as the grid survey predicts; the −0.005 line (0.86 µs) is
  sarpy's microsecond parsing of `CollectStart`.
- With the starting-range fix: median 0.000 dB, p5 / p95 −0.0027 / +0.0027 dB; 1 GCOV-only and
  2,103 MultiRTC-only pixels; tile correlation −0.0009 m along / −0.0001 m across.
- The calibrated radar-domain pixels agree before geocoding: 136.9 M pixels, max relative
  difference 1.2e-7, max phase difference 5.9e-8 rad, identical zero pattern.

## 7. What this does and does not establish

- Established: on six Capella stripmap scenes covering both look sides and both pass directions,
  the converted RSLCs reproduce the headers' closest-approach (INCA) geometry in isce3 to ≤ 0.04 mm
  against an independent implementation (with the COA time aligned), three pairs from two sites give the same phase convention, and
  isce3's GCOV runs on every beta0 RSLC; on a right-looking scene its output matches an
  independent ingest path (MultiRTC, one grid fix) to 99.80 % within 0.01 dB.
- Not established: an R-D interferogram (no repeat pair in the open-data InSAR set); sliding
  spotlight (also RGZERO, untested); any producer other than Capella; RUNW/GUNW on X-band;
  absolute geolocation against ground truth.
- The 3.4 cm stock geometry difference is explained, not removed: the two header descriptions of a
  pixel disagree by that much where the COA time is far from closest approach.

## 8. Reproduce

```
make data-capella-reinforcement         # 2.9 GB, anonymous HTTPS
make capella-rein-dem capella-rein-convert capella-rein-geometry
venv-sarkit/bin/python tools/sicd_rslc_geometry_check.py sarkit data/capella_reinforcement/rslc/<id>.h5 \
    --out data/capella_reinforcement/rslc/geom_sarkit_<id>.json        # host, per scene
python tools/sicd_rslc_geometry_check.py compare .../geom_isce3_<id>.json .../geom_sarkit_<id>.json
venv-sarkit/bin/python tools/sicd_coa_to_ca.py data/capella_reinforcement/rslc/<id>.h5 --out coa.xml
venv-sarkit/bin/python tools/sicd_rslc_geometry_check.py sarkit coa.xml --out geom_sarkit_coa_eq_ca_<id>.json
make capella-rein-rifg                   # Niscemi R-A and L-D, flat + noflat
docker compose run --rm -v $HOME/scratch/capella:/scratch_root dev python tools/rifg_fringe_rate.py \
    --flat /data/capella_reinforcement/rifg/<name>_flat/product.h5 \
    --noflat /data/capella_reinforcement/rifg/<name>_noflat/product.h5 \
    --range-off /scratch_root/<name>_flat/geo2rdr/freqA/range.off \
    --rslc /data/capella_reinforcement/rslc/<ref id>.h5 --out fringe.json --png quicklook.png
docker compose run --rm dev python scripts/repro_bandpass_ratio_check.py
make capella-rein-gcov capella-rein-multirtc
```
