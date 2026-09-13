# A/B: upstream PR #379 (validMask + integrated mask vectorization) vs its merge-base

Date: 2026-09-13. Context: upstream
[isce3#379](https://github.com/isce-framework/isce3/pull/379)
(xhuang-jpl) adds per-polarization `validMask` datasets to the InSAR
products and integrates the row-wise `generate_insar_mask`
vectorization from
[isce3#359](https://github.com/isce-framework/isce3/pull/359) into
`python/packages/nisar/products/insar/utils.py`, extending it with a
sliding-window `inputDataExceptionMask` reader. This bundle checks
PR #379 for regressions on a real NISAR frame before #359 is
downshifted to a tests-only PR.

## Variants

Full-package overlays of `python/packages/nisar` (install-tree copy
for schemas + `git archive` snapshot of the variant SHA), verified to
differ in exactly the 7 files PR #379 changes. The compiled `isce3`
extension stays the container install (develop `0.26.0-dev+2919e1c97`)
for both legs.

| variant | source | utils.py md5 |
|---|---|---|
| base | merge-base `0d1600d8b` (scalar loop, blob `0643bde0c`) | `af6456d8983271e0feb1b1696232d993` |
| pr379 | PR #379 head `0c2ff0af5` (`git fetch upstream pull/379/head`) | `24790102a0057d6e7840c995b3608f0d` |

## Harness

Same standalone seeded-scratch `prepare_insar_hdf5` run as
[`../pr358-realdata-ab-20260827/`](../pr358-realdata-ab-20260827/):
NISAR ASC 139/019 Boso pair, runconfig
`insar_gunw_ASC139_019_20260705_20260717_gpu.yaml` (frequency A, HH
only; RIFG interferogram grid 6840 x 10581 = 72.4 Mpx), geo2rdr seed
`bench36_step2_20260826/phase0/scratch/geo2rdr` (same instance as the
08-27 batch) mounted read-only. One run per variant, sequential,
otherwise idle host (NucBox EVO-T1, Core Ultra 9 285H, 93 GiB).
The reference/secondary RSLC granules carry a **uint8**
`inputDataExceptionMask` (pre-#370 spec), so the pr379 leg exercises
the old-RSLC fallback path of the new per-pol validMask code.

## Timing + memory (`run_*.log`, `journal_*.log`)

| variant | prepare_insar_hdf5 (journal) | vs scalar | wall (`time -v`) | peak RSS |
|---|---|---|---|---|
| base (scalar loop) | 417.953 s | 1x | 419.9 s | 12.46 GiB |
| pr379 | 84.813 s | 4.93x | 86.8 s | **6.61 GiB** |

- The scalar baseline sits inside the documented window of this
  harness (406.3 s on 08-27, 435.4 s on 08-13).
- The #359 speedup class survives the integration (4.93x here vs
  5.15x for #359's utils.py alone on 08-27; the delta is the added
  per-pol validMask work).
- Peak RSS drops to 6.61 GiB — below both the scalar baseline
  (12.46 GiB) and #359 (10.13 GiB on 08-27). PR #379's sliding-window
  `_RSLCInputDataExceptionMask` reader avoids the full ~2.2 GB x2
  exception-mask loads that both the scalar loop and #359 perform.

## Bitwise product comparison (`compare_base_pr379.txt`)

`compare_prepare_products.py` (from
[`../pr358-realdata-ab-20260827/`](../pr358-realdata-ab-20260827/)):
every HDF5 dataset in the GUNW skeleton and the RIFG/RUNW scratch
skeletons, byte-for-byte:

- 558 common datasets; the only "differs" entries are the known
  run-varying metadata (`processingDateTime` x3,
  `runConfigurationContents` x3). **Every science dataset, including
  all `mask` layers, is bitwise-identical to the scalar merge-base
  run.**
- only-in-b: the 7 new `validMask` datasets (RIFG/RUNW interferogram
  + pixelOffsets, GUNW unwrapped/wrapped/pixelOffsets), i.e. exactly
  the feature being added.

## validMask checks (`validmask_pr379.txt`, `check_validmask_percheck.py`)

Radar-grid validMask values are {0,1,2,3} (bit 1 = reference valid,
bit 0 = secondary valid). The initial spot-check compared nonzero
counts against the co-located `mask`; the per-pixel check added in
the review round (`check_validmask_percheck.py`, output in
`validmask_percheck_output.txt`) is the stronger statement: HH
validMask equals the mask's sub-swath validity bits
(`2*(ref digit > 0) + (sec digit > 0)`) at **every pixel** of all
four RIFG/RUNW radar-grid layers (0 mismatches; RIFG interferogram:
69,341,858 px either-valid / 68,664,482 both-valid; pixelOffsets:
368,611 / 366,145). The same script reproduces the legacy fallback
finding on a minimal grid: with a uint8 or absent
`inputDataExceptionMask`, `mask` is 11 everywhere while
`extract_pol_valid_mask` yields HH=3 and HV/VH/VV=0. GUNW grid
validMask skeletons are all-0 with `_FillValue = 255`, awaiting the
geocode step (same convention as the existing `mask` skeleton) — the
geocoding of the new validMask planes is **not** covered by this
bundle.

## Regression-test forward-check

The #359 regression tests, adapted for a tests-only PR (optional
`_subswath_numbers` import + tuple-tolerant `unpack_mask` shim):

| target | result |
|---|---|
| merge-base overlay (scalar loop) | 7 passed, 4 skipped (helper tests skip: no vectorized helper) |
| PR #379 overlay | **11 passed** (helper tests activate; tuple return handled) |

PR #379's integrated implementation passes the full adversarial
suite (half-integer offset landings, empty sub-swath arrays,
no-sub-swath-info, MSB exception bytes, rounding-rule divergence).

## Non-manifesting findings (code-level, out of scope for this A/B)

Two defects found by review of PR #379 do **not** manifest on this
dataset (frequency A only, HH only) and are therefore not measured
here: the per-pol validMask update loop for pixelOffsets sits outside
the frequency loop in `InSAR_L1_writer.add_pixel_offsets_to_swaths_group`
(multi-frequency products would leave frequency A's validMask
unwritten), and the uint8-RSLC fallback sets only the HH bit of the
per-pol validity word (non-HH polarizations of pre-#370 RSLCs would
read as all-invalid). Both are documented for the upstream review.
