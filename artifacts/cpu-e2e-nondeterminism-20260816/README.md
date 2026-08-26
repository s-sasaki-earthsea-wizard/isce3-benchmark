# cpu-e2e-nondeterminism-20260816 — CPU E2E run-to-run nondeterminism

Evidence bundle for issue
[#36](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/36):
the CPU InSAR workflow (RSLC → GUNW) is **not run-to-run bitwise
reproducible** — CPU Ampcor (`dense_offsets`) emits float32 last-bit
(ULP-level) differences between identical sequential runs — while the
**valid-support unwrapped phase is functionally reproducible** (zero 2π
flips on CC>0, agreement ≤ 1.03e-4 rad; Step 1 result below).

Origin: this surfaced during the CPU E2E A/B for the vectorized
`generate_insar_mask()`
([isce3#354](https://github.com/isce-framework/isce3/issues/354) →
PR [isce3#359](https://github.com/isce-framework/isce3/pull/359); local
issue [#32](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/32),
A/B evidence in
[`../prepare-insar-mask-ab-20260813/`](../prepare-insar-mask-ab-20260813/)).
The A/B could not be closed bitwise — and the pristine-vs-pristine control
below shows why: the nondeterminism pre-exists in the CPU pipeline and is
not caused by the patch.

## Headline results

1. **A/B pair (2026-08-16, control = pristine v0.25.16, treat = vectorized
   `utils.py`)**: 530/558 HDF5 datasets bitwise identical — including
   **every mask layer** (the datasets the patch writes). The 28 differing
   datasets are `pixelOffsets` + downstream cascade (coherence,
   wrapped/unwrapped igram, iono screens) + run-varying metadata
   (`processingDateTime`, `runConfigurationContents`).
   → `compare_bitwise_output.txt`, `quantify_diffs_output.txt`
2. **Offset deltas are ULP, not Ampcor quanta**: max 1.19e-07 px on the
   radar grid; deltas × 32 ≈ 1e-06, i.e. far below the 1/32-px
   quantization step. → `quantify_diffs_output.txt`
3. **Attribution (pristine-vs-pristine)**: the unpatched 2026-08-16
   control vs the unpatched 2026-08-10 `run2` (same runconfig, same
   v0.25.16 build) reproduces the identical difference class
   (offsets ULP / unwrappedPhase 56.6 % bitwise-differing with max ≈ 2π /
   connectedComponents exactly 1 px). The patch is exonerated; the
   nondeterminism is pre-existing. → `pristine_vs_pristine_output.txt`
4. **Step 1 gauge decomposition (all three run pairs, radar-grid RUNW)**:
   on the common valid support (CC>0 in both runs: 4,980,716 px, a single
   connected component in every run) there are **zero pixels with
   |Δ| > π**, max |Δ| ≤ 1.03e-4 rad, gauge purity 1.000000, residual RMS
   1.73–1.81e-5 rad. ±1-cycle differences exist **only** at isolated
   CC==0 (invalid/masked) pixels: 604 / 881 / 767 per pair.
   Ionosphere-screen deltas ≤ 4.3e-4 rad (~2 µm LOS at L-band).
   → `step1_gauge_decompose_output.txt`, `step1b_cc0_and_iono_output.txt`,
   independently spot-checked by `verify_step1_claim_output.txt`

Result 4 **corrects the issue body's original headline** ("±2π region
re-referencing over ~57–92 % of pixels"): that figure conflated
bitwise-differing pixels (float dust at the 1e-5 rad level) with
2π-differing pixels. On this evidence SNAPHU's solution is stable on valid
support and the upstream ULP noise propagates essentially linearly. See
the 2026-08-16 correction comments on issue #36.

**Contrast**: the GPU E2E pair on the same host (2026-08-13,
[`../prepare-insar-mask-ab-20260813/`](../prepare-insar-mask-ab-20260813/))
was bitwise identical in all 558 datasets — the GPU pipeline is
run-to-run deterministic here while the CPU one is not. Consequence:
bitwise E2E identity is unusable as a CPU A/B acceptance criterion;
"CC>0 phase agreement ≤ ~1e-4 rad + zero flips" is the usable substitute.

Standing caveats: 1 scene, 1 config, single-tile SNAPHU, 3 runs (the
three pairwise comparisons are not independent samples).

## Run setup

- Pair: NISAR L-band ASC 139/019 2026-07-05 → 2026-07-17, runconfig
  `insar_gunw_ASC139_019_20260705_20260717.yaml` (CPU,
  `gpu_enabled: false`), same discipline as the 2026-08-13 GPU E2E.
- Build: isce3 v0.25.16 source + `isce3-build-v0.25.16`, run in the
  isce3-benchmark dev container (`run_ab.sh` → `inner.sh`).
- Single variable = `utils.py` via copy-overlay (`/tmp/ov` prepended to
  `PYTHONPATH`; resolved module path echoed and guarded, marker counts
  0/3 recorded in `run_*.log`). `patched_utils.py`
  (sha256 `0e69e712…`, listed in `INPUT_SHA256SUMS.txt`) is
  byte-identical to `python/packages/nisar/products/insar/utils.py` on
  the isce3 PR branch `perf/vectorize-insar-mask` (isce3#359); the file
  itself is not duplicated here.
- Host: ew-s-sasaki-beacon-NucBox-EVO-T1, 16 threads, NVMe out/scratch,
  sequential control → treat (never concurrent), `/usr/bin/time -v`.
- Wall clock (from `run_*.err`): control 1:43:03, treat 1:34:30; max RSS
  ≈ 40.0 / 39.96 GiB.
- Timing (journal, recorded in issue #36; supplemental isce3#354
  evidence): `prepare_insar_hdf5` #1 511.684 → 117.081 s (**4.37x**),
  INSAR total 6178.607 → 5666.842 s; control reproduced the 2026-08-10
  CPU baseline within 3 s.
- `run2` (2026-08-10 unpatched CPU run) provenance: its full
  `insar.log` is already committed as
  [`../insar-timing-20260810/insar_cpu_run2.log.gz`](../insar-timing-20260810/insar_cpu_run2.log.gz).

## Files

| file | what |
|---|---|
| `run_ab.sh` / `inner.sh` | sequential A/B runner (host side / in-container side with overlay guard) |
| `compare_bitwise.py` → `compare_bitwise_output.txt` | 558-dataset byte-for-byte comparison, control vs treat (GUNW + RIFG/RUNW) |
| `quantify_diffs.py` → `quantify_diffs_output.txt` | per-dataset diff counts/magnitudes for the 6 differing science layers |
| `pristine_vs_pristine.py` → `pristine_vs_pristine_output.txt` | attribution control: 08-16 control vs 08-10 run2 (both unpatched), GUNW grids |
| `step1_gauge_decompose.py` → `step1_gauge_decompose_output.txt` | issue #36 Step 1: per-component 2π gauge decomposition, 3 run pairs |
| `step1b_cc0_and_iono.py` → `step1b_cc0_and_iono_output.txt` | Step 1 addendum: CC>0 vs CC==0 split + ionosphere-screen propagation |
| `verify_step1_claim.py` → `verify_step1_claim_output.txt` | independent spot-check of the Step 1 numbers (control vs treat) |
| `run_control.log` / `run_treat.log` | runner stdout (overlay path, marker count, PYSOLID etc.) |
| `run_control.err` / `run_treat.err` | runner stderr incl. `/usr/bin/time -v` block |
| `insar_e2e_control.log.gz` / `insar_e2e_treat.log.gz` | full workflow `insar.log` (journal timing) per run |
| `INPUT_SHA256SUMS.txt` | SHA-256 + locations of the 9 HDF5 inputs (not in repo, ~7.5 GiB) |

Known no-op in `pristine_vs_pristine.py`: the two GUNW `…/HH/mask` paths
in its `NAMES` list do not exist in **either** product (the "MISSING in
one file" lines fire for both); mask identity for control-vs-treat is
established by the 558-dataset bitwise pass instead.

## Reproducing the analysis outputs

The committed scripts use the in-container mount paths of the original
2026-08-16 session (`run_ab.sh` mounts `/ab`; the run2 products were
mounted at `/run2` and `/run2scratch`). The `*_output.txt` files here
were regenerated on 2026-08-26 directly on the host (Python 3.13.11,
h5py 3.16.0, numpy 2.2.6) from the
hash-pinned inputs of `INPUT_SHA256SUMS.txt` (the container environment
runs NumPy 1.26.4; the version difference does not matter here), using
copies of these scripts with only the path constants rewritten:

```
/ab/          → ~/scratch/cpu_e2e_ab_20260816/
/run2/        → ~/scratch/gunw_cpu_run2_out/
/run2scratch/ → ~/scratch/gunw_ASC139_019_cpu2/
```

Every value matches the numbers recorded in the issue #36 body and
comments on 2026-08-16 — the comparisons are byte/float64 diffs of
on-disk data, insensitive to the h5py/numpy version difference.

## Follow-up

Steps 2–4 of issue #36 (SNAPHU replay on pinned inputs, OMP=1/16
repetition probe, scaled-perturbation sweep, implications write-up)
remain open; see the plan-adjustment comments on the issue.
