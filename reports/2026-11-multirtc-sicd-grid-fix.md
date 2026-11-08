# MultiRTC SICD radar-grid fix, verified on Capella data

- Date: 2026-11-08
- Host: NucBox EVO T1 (Intel Core Ultra 9 285H, 16 CPUs, 93 GB RAM), the same
  host as the 2026-09 reports; GCOV and MultiRTC run on CPU.
- isce3: upstream `develop` `2919e1c9` (`0.26.0-dev+2919e1c9`), from-source
  container build, imported by both paths.
- MultiRTC: v0.5.4 `a0edba80a05c923b03ffae378e4a1faf293b0f0f` (BSD-3), installed
  `--no-deps` twice: as is (`make multirtc-setup`), and with
  `patches/multirtc/sicd-rgzero-radar-grid.patch` applied
  (`make multirtc-patched-setup`). sarpy 1.3.59 in both.
- The patch: three commits on `a0edba80` (`adfac02`, `67ab961`, `e7aa57b`,
  branch `fix/sicd-rgzero-radar-grid` of `s-sasaki-earthsea-wizard/MultiRTC`),
  the candidate upstream fix for the three radar-grid defects of
  `reports/2026-09-capella-rtc-gcov-vs-multirtc.md` §3.1. Patch file sha256
  `afcc4907…`. The run used an earlier export of the same commits with an
  AI-assistance trailer in the messages, since removed. Its code is identical:
  the patched checkout's `git diff HEAD` sha256 `e54cf4a3…` is the same for
  both files and is the value recorded in the run (`patches/multirtc/README.md`).
- Data: Capella open data. RTC: `CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055`
  (Mexico City, stripmap, left-looking, 19191 × 10631), Copernicus GLO-30 DEM,
  the same files for both paths. Radar-grid survey: the 7 Capella stripmap
  SICDs of the 2026-09 reports (4 left-looking, 3 right-looking).
- Evidence: `artifacts/multirtc-sicd-fix-20261108/`,
  `artifacts/multirtc-grid-survey-20261108-patched/`.

## Summary

The 2026-09 numbers for the three fixes came from runtime subclasses of the
v0.5.4 class (`tools/multirtc_capella_rtc.py`, variants `matched-tfix` and
`matched-tfix-rfix`). This report runs the actual patch instead, with no
subclass, and checks it on the same data.

- **Radar grid, 7 scenes.** With the patch, MultiRTC's sensing start, starting
  range and PRF equal the values the SICD defines, exactly (0 in double
  precision; PRF within one ulp). Its wavelength is c / ((Min + Max) / 2). On
  the 3 right-looking scenes the sensing start is the v0.5.4 value bit for bit,
  so the start-time change acts only where the azimuth axis is time-reversed.
- **RTC, Mexico City.** Against isce3 GCOV on the same SICD, DEM and isce3
  build, the patched MultiRTC places pixels 0.000 m / 0.000 m (along / across
  track) from GCOV, and **99.974 %** of 8,097,578 common pixels agree within
  0.01 dB (v0.5.4: 0.17 %).
- **Same radar grid as the prototype.** The patched grid is bit-identical to
  the `matched-tfix-rfix` prototype's except for the wavelength. The prototype
  agreed on 99.980 % of pixels, slightly more than the patch. The difference is
  in the orbit, not the wavelength (§4). The prototype built its orbit before
  moving the start time, so its state-vector times are rounded to 1 µs from a
  different start: 87 µm along track. A control run, patched but with the v0.5.4
  wavelength put back, is bit-identical to the patched run in all 8 output
  rasters. The wavelength does not reach the RTC output.
- **Reproducibility.** The same host re-ran the four v0.5.4 variants and the
  v0.5.4 grid survey. Every result field matches the committed 2026-09-24
  artifacts exactly. The one exception is a derived survey column whose
  computation this branch changed, which moved by 1 ulp (`repro_check.txt`).

## 1. The patch

| commit | `src/multirtc/sicd.py` at a0edba80 | v0.5.4 | patched |
|---|---|---|---|
| `adfac02` | L42 (`SicdSlc`, all SICD inputs) | `Min + Max / 2` | `(Min + Max) / 2` |
| `67ab961` | L169, L175 (`SicdRzdSlc`) | last column time at `NumCols`; PRF = `NumCols / (t_end − t_start)` | at `NumCols − 1`; PRF = `(NumCols − 1) / (t_end − t_start)` |
| `e7aa57b` | L173 (`SicdRzdSlc`) | `get_starting_range(0)` (SCP tangent plane, column 0) | `R_CA_SCP − (SCPPixel.Row − FirstRow) × Row.SS` |

The two lines of `67ab961` belong together. Moving only `last_col_time` would
scale the PRF by `NumCols / (NumCols − 1)`, a drift of about one line across
the image. With both lines changed, a linear `TimeCAPoly` gives the same PRF as
before, and a scene that is not time-reversed keeps its sensing start.
`get_starting_range()` has no caller after `e7aa57b` and is left in place.

Provenance is recorded from what was actually imported, not from the pinned SHA
(`tools/multirtc_provenance.py`: package path, hashes of its `*.py` files and of
`sicd.py`, the checkout's HEAD and diff hash). All 16 installed `*.py` files of
the v0.5.4 install match `git show a0edba80:src/…`. All 16 of the patched
install match the patched checkout, which differs from `a0edba80` only in
`sicd.py` and `CHANGELOG.md`.

## 2. Radar grid on 7 scenes

`tools/multirtc_grid_survey.py` builds MultiRTC's own `SicdRzdSlc` and compares
it with the SICD metadata. No pixels are read. In the table, v0.5.4 values come
first, then the patched values:

| scene | side | sensing start − SICD [lines] | starting range − RGZERO row 0 [m] | wavelength MultiRTC / SICD [cm] |
|---|---|---|---|---|
| C14 20240626150051 | L | −1.000 → 0 | −6.323 → 0 | 2.078 → 3.107 / 3.107 |
| C14 20240629134910 | L | −1.000 → 0 | −6.292 → 0 | 2.078 → 3.107 / 3.107 |
| C13 20260204114511 | R | 0 → 0 | −7.235 → 0 | 2.089 → 3.123 / 3.123 |
| C13 20260204183253 | L | −1.000 → 0 | −7.176 → 0 | 2.089 → 3.123 / 3.123 |
| C13 20260207104155 | R | 0 → 0 | −7.183 → 0 | 2.089 → 3.123 / 3.123 |
| C13 20260207172937 | L | −1.000 → 0 | −7.193 → 0 | 2.089 → 3.123 / 3.123 |
| C15 20260627144157 | R | 0 → 0 | −4.775 → 0 | 2.078 → 3.107 / 3.107 |

The patched zeros are exact in double precision. The PRF ratio to the SICD
line rate is 1 to within 1.1e-16. On the right-looking scenes, the patched
`sensing_start` is the v0.5.4 value bit for bit, and the PRFs differ by at most
1.8e-12 Hz.
v0.5.4: `artifacts/multirtc-grid-survey-20260924/`, reproduced on this host
except a 1-ulp change in the derived `multirtc_centre_ghz` column, which the
survey now computes from MultiRTC's wavelength instead of re-implementing
its formula. Patched: `artifacts/multirtc-grid-survey-20261108-patched/`.

## 3. RTC on Mexico City, against isce3 GCOV

Same comparison as `reports/2026-09-capella-rtc-gcov-vs-multirtc.md`
(`tools/compare_rtc.py`): radar grid against our beta0 RSLC, the map offset
the grid difference implies (rdr2geo, 25 points, our orbit, 2245 m), the
offset measured by tile correlation (biased estimator, see that report §3.2),
and gamma0 on the common support of the two 5 m grids. Options are matched
(input beta0, output gamma0, area projection, biquintic DEM, no shadow
masking) except `stock`.

| MultiRTC | sensing start − ours | starting range − ours | wavelength | offset predicted (along / toward near range) | offset measured | common pixels | within 0.01 dB | within 0.1 dB |
|---|---|---|---|---|---|---|---|---|
| v0.5.4, stock | −1.006 line | −6.323 m | 2.078 cm | −0.907 m / 9.667 m | −1.172 m / 8.843 m | 8,094,872 | 0.17 % | 1.75 % |
| v0.5.4, matched | −1.006 line | −6.323 m | 2.078 cm | −0.907 m / 9.667 m | −1.172 m / 8.843 m | 8,094,986 | 0.17 % | 1.75 % |
| prototype (`matched-tfix-rfix`) | −0.006 line | 0 | 2.078 cm | −0.006 m / 0.000 m | 0.000 m / 0.000 m | 8,097,578 | 99.980 % | 99.99994 % |
| **patched** (`patched-matched`) | −0.006 line | 0 | **3.107 cm** | −0.006 m / 0.000 m | 0.000 m / 0.000 m | 8,097,578 | **99.974 %** | 99.99994 % |
| control: patched, v0.5.4 wavelength | −0.006 line | 0 | 2.078 cm | −0.006 m / 0.000 m | 0.000 m / 0.000 m | 8,097,578 | 99.974 % | 99.99994 % |

Our RSLC's wavelength is 3.107 cm. The −0.006 line left after the fix, and the −6 mm
predicted along-track offset, are the 927 ns that sarpy drops from
`Timeline/CollectStart` (microsecond parsing). The patch does not address this
(§5).

For the patched run, gamma0 differs from GCOV by a median of +3.1e-7 dB
(p5 / p95 −0.00154 / +0.00154 dB). The support is 0 GCOV-only and 5,764
MultiRTC-only pixels, the same as the prototype. In the radar domain,
MultiRTC's complex beta0 raster still matches our beta0 RSLC to 1.2e-7 relative
and 6.0e-8 rad over 203,810,191 pixels.

## 4. Patched against the prototype

The two runs build the same `RadarGridParameters`: sensing start, PRF,
starting range, spacing, size, epoch and look side are equal in double
precision. They differ in:

1. **Wavelength:** 3.107 cm against 2.078 cm.
2. **Orbit:** a constant 87.2 µm along track at every sampled time. `get_orbit()`
   samples the ARP polynomial at `sensing_start + k` s, and each state vector's
   time goes through `datetime.timedelta`, which rounds to 1 µs. The prototype
   subclasses replaced `sensing_start` after the parent `__init__` had built
   the orbit, so its samples start from the v0.5.4 time. The fractional
   microseconds are 0.0818 µs there and 0.0939 µs with the patch. The difference,
   0.0121 µs × 7215.5 m/s, is 87 µm.

   The same arithmetic accounts for the orbit difference against our RSLC. The
   CollectStart nanoseconds (0.927 µs → 6.69 mm) plus the rounding (0.59 mm,
   prototype; 0.68 mm, patched) give 7.28 mm and 7.37 mm, which is what
   `compare_rtc.py` measures.

The control run (`wavelength_control.py`) keeps the patched grid and orbit and
puts back only the v0.5.4 wavelength. In all 8 output rasters (gamma0, number of
looks, both RTC area factors, mask, incidence angle, local incidence angle,
interpolated DEM) it is **bit-identical** to the patched run
(`runs_patched_vs_control.json`). Its GCOV comparison differs from the patched
one only in the recorded wavelength. With the zero-Doppler `LUT2d()` of
`SicdRzdSlc`, the wavelength reaches `RadarGridParameters` and
`get_radar_grid()` but not the geocoded output, so fix `adfac02` changes
metadata and any phase use, not gamma0.

That leaves the orbit as the only difference between the patched run and the
prototype (`runs_patched_vs_prototype.json`; the control against the prototype
gives the same numbers):

| raster | bit-identical | median abs difference | within 0.001 dB | within 0.01 dB | beyond 0.1 dB |
|---|---|---|---|---|---|
| gamma0 | 0.16 % | 0.00020 dB | 93.10 % | 99.9973 % | 4 of 8,103,342 pixels (max 2.94 dB, one bright pixel) |
| number of looks | 0.04 % | 5.3e-5 relative | 99.75 % | 99.995 % | 40 pixels |
| incidence angle, DEM | 100 % | 0 | | | |

The area-projection RTC therefore responds to an 87 µm along-track orbit
change (about 1e-4 of an azimuth pixel) at the 1e-4 dB level. This is also why
the patched run agrees with GCOV on 0.007 percentage points fewer pixels than
the prototype: its orbit is 7.37 mm from ours, against 7.28 mm.

## 5. What this does and does not establish

- The patch makes MultiRTC's RGZERO radar grid match the SICD definition on 7
  Capella stripmap scenes, both look directions, three satellites. With the
  patch, the Mexico City RTC agrees with isce3 GCOV to the level the 2026-09
  prototype reached.
- On right-looking scenes the patched grid is the v0.5.4 sensing start and PRF
  (bit for bit) plus the corrected starting range. That is the grid of the
  `matched + starting-range fix` run on Niscemi
  (`reports/2026-09-capella-reinforcement.md` §6.1, 99.80 % within 0.01 dB).
  Its orbit is also sampled from the same start. The patched RTC was not
  re-run there.
- ICEYE inputs go through the same `SicdRzdSlc`, and Umbra (`SicdPfaSlc`)
  shares the wavelength line. Neither was tested.
- Not fixed: the sub-microsecond `CollectStart` (−0.006 line here) and the
  1 µs rounding of orbit state-vector times (§4, below 1 mm). Both come from
  the epoch and times passing through Python `datetime`.
- MultiRTC's test suite has no SICD fixtures. The patch adds no test.

## 6. Reproduction

On the NAS-backed host, `data/capella_*` are symlinks into the NAS, which the
dev container sees at the same path. This makes no difference to the results.

```bash
make data-capella data-capella-reinforcement capella-dem
make capella-convert-beta0 capella-gcov
make multirtc-setup capella-multirtc capella-rtc-compare      # v0.5.4 variants
make multirtc-patched-setup capella-multirtc-patched          # the patch
make multirtc-grid-survey-patched                             # 7-scene survey
# stock survey re-run and the 2026-09-24 comparison:
docker compose run --rm -T dev bash -c 'export PYTHONPATH=/data/external/multirtc-site:$PYTHONPATH; \
    python tools/multirtc_grid_survey.py /data/capella_mexico_city/*.ntf /data/capella_reinforcement/*.ntf \
    --out /data/capella_mexico_city/survey_stock_rerun.json'
python3 artifacts/multirtc-sicd-fix-20261108/repro_check.py
```

Control run and raster comparisons (`artifacts/multirtc-sicd-fix-20261108/`):

```bash
docker compose run --rm -T dev bash -c 'set -e; \
    export PYTHONPATH=/data/external/multirtc-site-patched:$PYTHONPATH \
           MULTIRTC_CHECKOUT=/data/external/MultiRTC-a0edba8-patched; \
    C=/data/capella_mexico_city; w=$C/multirtc/20240626/patched-stock-wavelength; mkdir -p $w; \
    python artifacts/multirtc-sicd-fix-20261108/wavelength_control.py rtc \
        $C/CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055.ntf \
        --variant patched-stock-wavelength --dem $C/dem.tif --resolution 5 --work-dir $w; \
    python tools/compare_rtc.py --gcov $C/gcov/20240626/gcov_20240626.h5 \
        --rslc $C/rslc_beta0/20240626.h5 --multirtc-run $w \
        --out $C/rtc_compare/20240626_patched-stock-wavelength.json'
docker compose run --rm -T dev bash -c 'R=/data/capella_mexico_city/multirtc/20240626; \
    A=artifacts/multirtc-sicd-fix-20261108; \
    python $A/compare_runs.py $R/patched-matched $R/patched-stock-wavelength > $A/runs_patched_vs_control.json; \
    python $A/compare_runs.py $R/patched-matched $R/matched-tfix-rfix > $A/runs_patched_vs_prototype.json; \
    python $A/compare_runs.py $R/patched-stock-wavelength $R/matched-tfix-rfix > $A/runs_control_vs_prototype.json'
```

Wall times on this host: GCOV 288 s; the four v0.5.4 variants 2100 s; the
patched variant with its GCOV comparison 573 s; the control 559 s.
