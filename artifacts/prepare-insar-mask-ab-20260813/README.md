# prepare-insar-mask-ab-20260813 — vectorized generate_insar_mask A/B

A/B evidence for the fix proposed out of issue
[#32](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/32):
vectorizing `generate_insar_mask()`'s pure-Python per-pixel loop
(identified in [`../prepare-insar-profile-20260813/`](../prepare-insar-profile-20260813/)
as 89 % of `prepare_insar_hdf5`'s samples).

## Patch under test

isce3 branch `perf/vectorize-insar-mask`, single commit `6d1fbe35b`
(base upstream `develop` @ `0a8df45dd`), touching ONLY
`python/packages/nisar/products/insar/utils.py` (+123/−52):

- sub-swath membership via the existing bulk
  `SubSwaths.get_valid_samples_array` pybind API + numpy interval
  tests (`_subswath_numbers`), preserving `get_sample_sub_swath`
  semantics (out-of-bounds → 0, first-match-wins, empty-array
  short-circuit, no-info → 1);
- both rounding rules preserved exactly (`int(x+0.5)` truncation →
  `np.trunc`; banker's `round()` → `np.rint`);
- exception-mask bytes widened to uint32 before the `<< 16` / `<< 8`
  shifts — NEP-50-safe, i.e. also fixes the
  [isce3#335](https://github.com/isce-framework/isce3/issues/335)
  bit-dropout under NumPy ≥ 2.0 (this environment is NumPy 1.26.4,
  where old and new behaviour coincide).

## Results

Same standalone seeded-scratch harness as the profiling run (same
runconfig, same read-only `geo2rdr` offsets seed, NVMe out/scratch,
develop build `./isce3-build`); **single variable = `utils.py`**
(treatment overlays the patched file on top of the pristine install
tree via `PYTHONPATH`; control uses the install tree as-is — the
resolved module path is echoed in each run log).

| | prepare_insar_hdf5 (journal) | py-spy samples | mask share |
|---|---|---|---|
| control (scalar loop) | **435.356 s** | 40,340 | 88.4 % |
| treatment (vectorized) | **86.810 s** | 7,562 | 39.7 % (≈ 30 s: GDAL row reads + exception-mask loads + numpy) |
| | **5.02x** | | |

- **Product equivalence**: all 558 datasets across the RIFG/RUNW
  skeletons and the GUNW product compared bitwise between control and
  treatment — every science dataset identical, including all `mask`
  layers. The only differing datasets are run-varying metadata that
  also differ between two *pristine* runs: `processingDateTime`,
  `runConfigurationContents` (embeds `repr()` object addresses), and
  the NaN-valued ionosphere `highBandBandwidth`/`lowBandBandwidth`
  (NaN ≠ NaN).
- **Unit-level equivalence**: `scripts/verify_insar_mask_vectorization.py`
  (this PR) checks the vectorized function bitwise against a frozen
  verbatim copy of the scalar loop on fixtures covering the rounding
  edges (exact k+0.5, negative offsets), empty/missing sub-swath
  layouts, out-of-swath rows/columns, out-of-bounds secondary indices
  and high exception-mask bits: 7/7 PASS (`verify_output.txt`).
- In-workflow projection: occurrence #1 562.5 s − 348.5 s saved ≈
  **214 s**, i.e. roughly −350 s on BOTH the CPU and GPU InSAR wall
  (the stage is CPU/GPU parity). To be confirmed by an in-workflow
  A/B before any upstream headline number.

## Files

| file | what |
|---|---|
| `verify_output.txt` | unit equivalence run (7 cases, PASS) |
| `insar_{control,treat}.log.gz` | journal logs (435.356 s / 86.810 s) |
| `time_v_{control,treat}.txt` | wall clock + peak RSS (`/usr/bin/time -v`) |
| `pyspy_{control,treat}.collapsed.txt.gz` | py-spy raw (100 Hz, nonblocking) |
| `attribution_{control,treat}.txt` | digests via `../prepare-insar-profile-20260813/analyze_prepare_collapsed.py` |

## Regenerate

Control/treatment differ only in the overlay preamble (treatment):

```bash
# inside the container command, before invoking the workflow:
mkdir -p /tmp/ov && cp -r /opt/isce3-build/install/packages/nisar /tmp/ov/nisar
cp /opt/isce3-src/python/packages/nisar/products/insar/utils.py \
   /tmp/ov/nisar/products/insar/utils.py     # patched source tree
export PYTHONPATH=/tmp/ov:$PYTHONPATH
```

(The naive overlay `ln -s <src>/nisar /tmp/ov/nisar` breaks:
`workflows/schemas/*.yaml` exist only in the install tree, not in
source. The unit test may use the symlink form since it never loads a
runconfig.)

Run command otherwise identical to
[`../prepare-insar-profile-20260813/README.md`](../prepare-insar-profile-20260813/README.md).
