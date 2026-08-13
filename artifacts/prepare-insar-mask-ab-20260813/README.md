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

## In-workflow (E2E) A/B — added same day

Full `python3 -m nisar.workflows.insar` GPU runs (run2 environment:
v0.25.16 build, NVMe out/scratch, same runconfig; NO py-spy). Both
runs use the copy-overlay so the only difference is the `utils.py`
content (pristine vs patched; the run logs echo the resolved module
path and a `_subswath_numbers` marker count of 0 / 3).

| journal stage | control | treatment | delta |
|---|---|---|---|
| prepare_insar_hdf5 #1 (freq A) | 464.222 s | **87.516 s** | -376.7 s (**5.30x**) |
| prepare_insar_hdf5 #2 (iono, incl. nested rdr2geo/geo2rdr) | 87.834 s | 73.480 s | -14.4 s |
| INSAR total | 3864.966 s | 3687.217 s | **-177.7 s** (1.05x) |

The gap between the -376.7 s stage delta and the -177.7 s
end-to-end delta is run-to-run variance on stages the patch cannot
touch: rubbersheet 406.9 -> 534.6 s (+128 s; documented 224-408 s
variance stage) and geo2rdr 120.4 -> 180.3 s (+60 s). Every other
stage sits at ~1.0x (full bracket table:
`table_e2e_control_treat.md`; accounting closure symmetric at ~1.3 %
in both runs). Holding those two stages at control values, the
end-to-end delta reproduces the stage delta (~ -365 s).

**E2E product equivalence**: all 558 datasets across the RIFG/RUNW
skeletons and the final GUNW compared bitwise between the two full
pipeline runs — every science dataset identical (interferograms,
coherence, offsets, unwrapped phase, all masks; NaN positions
identical). Only `processingDateTime` and the repr-address
`runConfigurationContents` differ, as between any two runs. This
doubles as a same-host GPU pipeline determinism check.

Reference walls from the 2026-08-10 report (same environment):
GPU 3959 s, prepare#1 562.5 s — both control values here sit within
the documented run-to-run variance of those.

## Files

| file | what |
|---|---|
| `verify_output.txt` | unit equivalence run (7 cases, PASS) |
| `insar_{control,treat}.log.gz` | journal logs (435.356 s / 86.810 s) |
| `time_v_{control,treat}.txt` | wall clock + peak RSS (`/usr/bin/time -v`) |
| `pyspy_{control,treat}.collapsed.txt.gz` | py-spy raw (100 Hz, nonblocking) |
| `attribution_{control,treat}.txt` | digests via `../prepare-insar-profile-20260813/analyze_prepare_collapsed.py` |
| `insar_e2e_{control,treat}.log.gz` | E2E journal logs (INSAR 3864.966 s / 3687.217 s) |
| `time_v_e2e_{control,treat}.txt` | E2E wall clock + peak RSS |
| `table_e2e_control_treat.md` | full per-stage bracket table (`tools/parse_insar_timing.py`) |

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
