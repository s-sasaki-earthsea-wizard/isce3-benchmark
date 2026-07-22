# GCOV bring-up: comparison against the official granule

All numbers produced by `scripts/compare_gcov_nisar.py` (dB domain, overlap
window, pixels valid in both products). Reference:
`NISAR_L2_PR_GCOV_025_125_A_017_4005_DHDH_A_20260716T203701_20260716T203721_P05023_N_P_J_001.h5`

## freqB 80 m smoke (`make gcov-freqB`)

```
freqB/HHHH: ours 3656x2655 @ (80,-80) | ref 4374x4302 | grid offset (186.000000, 180.000000) px
freqB/HHHH: overlap 2655x3656 finite ours=0.5521 ref=0.5819 common=0.5521
freqB/HHHH:   ours dB mean=-16.247 sd=7.644 | ref dB mean=-16.265 sd=7.622
freqB/HHHH:   diff dB mean=+0.0172 sd=0.3646 | corr=0.998867
freqB/HVHV: ours 3656x2655 @ (80,-80) | ref 4374x4302 | grid offset (186.000000, 180.000000) px
freqB/HVHV: overlap 2655x3656 finite ours=0.5521 ref=0.5819 common=0.5521
freqB/HVHV:   ours dB mean=-21.918 sd=7.532 | ref dB mean=-21.936 sd=7.507
freqB/HVHV:   diff dB mean=+0.0174 sd=0.3556 | corr=0.998886
```

## freqA 10 m baseline (`make gcov-freqA`; no TEC, RSLC-embedded orbit)

```
freqA/HHHH: ours 29241x21228 @ (10,-10) | ref 34992x34416 | grid offset (1488.000000, 1447.000000) px
freqA/HHHH: overlap 21228x29241 finite ours=0.5521 ref=0.5818 common=0.5521
freqA/HHHH:   ours dB mean=-16.994 sd=5.403 | ref dB mean=-16.937 sd=5.408
freqA/HHHH:   diff dB mean=-0.0560 sd=2.2059 | corr=0.916737
freqA/HVHV: ours 29241x21228 @ (10,-10) | ref 34992x34416 | grid offset (1488.000000, 1447.000000) px
freqA/HVHV: overlap 21228x29241 finite ours=0.5521 ref=0.5818 common=0.5521
freqA/HVHV:   ours dB mean=-22.624 sd=5.469 | ref dB mean=-22.569 sd=5.482
freqA/HVHV:   diff dB mean=-0.0552 sd=2.0978 | corr=0.926625
```

## freqA 10 m same-ancillary rerun (`make gcov-freqA-anc`; official TEC + MOE orbit)

wall 31:50.40, peak RSS 54.3 GB

```
freqA/HHHH: ours 29241x21228 @ (10,-10) | ref 34992x34416 | grid offset (1488.000000, 1447.000000) px
freqA/HHHH: overlap 21228x29241 finite ours=0.5521 ref=0.5818 common=0.5521
freqA/HHHH:   ours dB mean=-16.974 sd=5.405 | ref dB mean=-16.937 sd=5.408
freqA/HHHH:   diff dB mean=-0.0363 sd=1.1814 | corr=0.976128
freqA/HVHV: ours 29241x21228 @ (10,-10) | ref 34992x34416 | grid offset (1488.000000, 1447.000000) px
freqA/HVHV: overlap 21228x29241 finite ours=0.5521 ref=0.5818 common=0.5521
freqA/HVHV:   ours dB mean=-22.605 sd=5.474 | ref dB mean=-22.569 sd=5.483
freqA/HVHV:   diff dB mean=-0.0356 sd=1.1471 | corr=0.978085
```

## Attribution conclusion

Supplying the official ancillaries (TEC ionospheric correction + MOE orbit)
and changing nothing else moves freqA agreement from corr 0.917/0.927
(diff σ ≈ 2.2/2.1 dB) to **0.976/0.978** (σ ≈ 1.18/1.15 dB) — i.e. the
ionosphere/orbit configuration delta is the dominant contributor (~70 % of
the difference variance). freqB is insensitive (0.9989) because the same
metre-level shift is sub-pixel at 80 m posting. The residual σ ≈ 1.2 dB is
consistent with SAS version differences (official: PCM r05.02.2 /
PGE r05.02.3 vs our isce3 develop build) and DEM handling differences
(NISAR DEM v1.2 staging vs dem_stitcher stitch — same COP-DEM content in
this AOI); shift-field estimation for the residual is tracked separately.
