# Real-data A/B: upstream PR #358 vs #359 `generate_insar_mask` on a NISAR frame

Date: 2026-08-27. Completes the real-data leg of the
[`../pr358-crosscheck-20260827/`](../pr358-crosscheck-20260827/)
synthetic comparison: the same three `utils.py` variants, measured
inside a real standalone `prepare_insar_hdf5` run on a NISAR L-band
frame, plus bitwise product comparison for BOTH vectorized
implementations against the scalar baseline.

## Setup

- Harness: the 2026-08-13 standalone seeded-scratch prepare run
  ([`../prepare-insar-profile-20260813/`](../prepare-insar-profile-20260813/)
  "Regenerate" section), minus py-spy: develop build
  (`./isce3-build`, container isce3 `0.26.0-dev+2919e1c97`), runconfig
  `insar_gunw_ASC139_019_20260705_20260717_gpu.yaml` (NISAR ASC
  139/019 Boso pair, freq A; RIFG igram grid 6840 x 10581 = 72.4 Mpx,
  RUNW 2565 x 4069, pixel-offset masks 545 x 703).
- geo2rdr seed: `~/scratch/bench36_step2_20260826/phase0/scratch/geo2rdr`
  (freq A 17 GB x2 + freq B 2.1 GB x2, from the bench#36 Step-2
  phase-0 CPU run of 2026-08-26) mounted read-only at
  `/scratch/geo2rdr`. NOTE: a different seed instance than the
  (deleted) one used on 08-13, so absolute mask bytes are not
  comparable ACROSS sessions; all three variants in THIS batch read
  the same seed, so the intra-batch A/B is single-variable.
- Overlay: copy of the install-tree `nisar` package (workflow schemas
  live only there) with `utils.py` swapped per variant,
  `PYTHONPATH`-prepended; each `run_<variant>.log` echoes the
  resolved module path and md5:
  - pristine = develop blob `0643bde0c` (`af6456d8...`)
  - pr358 = PR #358 head `bdb597462` (`9db496b4...`)
  - pr359 = PR #359 head `dbb4e8dbc` (`28a1c27c...`)
- One run per variant, sequential, otherwise idle host
  (NucBox EVO-T1, Core Ultra 9 285H, 93 GiB, RTX 5080).

## Timing + memory (`run_<variant>.log`, `journal_<variant>.log`)

| variant | prepare_insar_hdf5 (journal) | vs scalar | wall (`time -v`) | user / sys CPU | peak RSS |
|---|---|---|---|---|---|
| pristine (scalar loop) | 406.252 s | 1x | 408.6 s | 432.9 / 17.4 s | 12.40 GiB |
| pr358 (full-grid) | 92.873 s | 4.37x | 95.1 s | 143.7 / 19.6 s | **17.90 GiB** |
| pr359 (row-wise) | 78.922 s | 5.15x | 81.1 s | 136.2 / 11.9 s | **10.13 GiB** |

- The pristine journal time (406.3 s) sits inside the documented
  range of this harness (435.4 s on 08-13, 446.5 s profiled;
  in-workflow occurrence #1 464-562 s).
- **Peak RSS is the decisive real-data difference**: pr358 runs the
  step at 17.9 GiB — +5.5 GiB over the scalar baseline and +7.8 GiB
  over pr359 — from the concurrent full-grid temporaries (579 MB
  each at 72.4 Mpx). pr359 lands BELOW the scalar baseline because
  the scalar loop's 72.4M-element Python list of ints (~2 GiB) is
  gone. The ~12.4 GiB floor common to all three is the workflow's
  own working set (RSLC-grid exception masks etc.).
- Wall difference between the two vectorizations is real but
  secondary at step scale: 95.1 vs 81.1 s (~1.2x; the mask loop is
  diluted by ~40 s of non-mask prepare work).
- Minor page faults are similar across variants (5.2-6.0 M;
  workflow-wide loads dominate), unlike the isolated synthetic
  microbenchmark — the workflow-scale evidence for the memory story
  is RSS, not fault counts.

## Bitwise product equivalence (`compare_pristine_pr35{8,9}.txt`)

`scripts/compare_prepare_products.py` (generalized from the 08-16
CPU-E2E `compare_bitwise.py`): every HDF5 dataset in the GUNW
skeleton (`out/product.h5`) and the RIFG/RUNW scratch skeletons,
byte-for-byte, pristine vs each vectorized run:

| pair | datasets | differing |
|---|---|---|
| pristine vs pr358 | 558 | 6 |
| pristine vs pr359 | 558 | 6 |

The 6 differing datasets are identical in kind for both pairs and
are the known run-varying metadata (`processingDateTime` x3 products,
`runConfigurationContents` x3 — embeds `repr()` object addresses),
i.e. **every science dataset, including all mask layers, is
bitwise-identical for BOTH implementations** on real NISAR granules
with real (NaN-bearing) offset rasters. PR #358's bit-for-bit claim
holds on NISAR data; the three variants are output-equivalent.

## Files

| file | what |
|---|---|
| `run_{pristine,pr358,pr359}.log` | overlay md5 echo + `/usr/bin/time -v` |
| `journal_{pristine,pr358,pr359}.log` | workflow journal (`insar.log`) |
| `compare_pristine_pr358.txt` | bitwise comparison, 558 datasets |
| `compare_pristine_pr359.txt` | bitwise comparison, 558 datasets |
