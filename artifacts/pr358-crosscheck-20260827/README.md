# Cross-check: upstream PR #358 vs PR #359 `generate_insar_mask` vectorization

Date: 2026-08-27. Context: upstream
[isce3#358](https://github.com/isce-framework/isce3/pull/358)
(lijun99) and
[isce3#359](https://github.com/isce-framework/isce3/pull/359)
(s-sasaki-earthsea-wizard) independently vectorize the same per-pixel
Python loop in `python/packages/nisar/products/insar/utils.py`
(`generate_insar_mask`). On 2026-08-26 lijun99 split #358 per
maintainer request and said he would review #359 before deciding
whether to resubmit his `prepare_insar_hdf5` part. This bundle
measures his implementation on our harness and checks both
implementations against a scalar oracle.

## Variants

All three are the same file swapped into an overlay copy of the
`nisar` package (`build/pr358_crosscheck/ov_<variant>/`, gitignored);
the compiled `isce3` extension stays the container install
(`0.26.0-dev+2919e1c97`, develop).

| variant | source | utils.py md5 |
|---|---|---|
| pristine | develop blob `0643bde0c` (common diff base of both PRs) | `af6456d8983271e0feb1b1696232d993` |
| pr358 | PR #358 head `bdb597462` (`git fetch upstream pull/358/head`) | `9db496b43112a2803c385dce024cb723` |
| pr359 | PR #359 head `dbb4e8dbc` (branch `perf/vectorize-insar-mask`) | `28a1c27cdb681896abf210ddddb376e0` |

## Environment

- Host: NucBox EVO-T1 (Intel Core Ultra 9 285H, 16 threads, 93 GiB),
  isce3-benchmark dev container, python 3.12, numpy 1.26.4,
  gdal 3.12.3.
- Scripts: `scripts/crosscheck_insar_mask_oracle.py` (this branch)
  and `scripts/repro_insar_mask_timing.py` (unchanged, from the #354
  evidence bundle).

## 1. Oracle check (`crosscheck_<variant>.txt`)

`crosscheck_insar_mask_oracle.py` compares whichever
`generate_insar_mask` resolves on `PYTHONPATH` against a fully
self-contained scalar oracle (pybind `SubSwaths.get_sample_sub_swath`
+ Python-int bit packing; imports nothing from the module under test,
so it runs against #358's re-signatured helpers as well). Six
synthetic cases with adversarial offsets (exact `k + 0.5` landings,
empty sub-swath arrays, out-of-swath pushes).

Seven synthetic cases (the `file_style_no_info` case was added in a
follow-up after a reachability question from Syota; the original run
had six).

| variant | result |
|---|---|
| pristine | 7/7 PASS (oracle self-validation against the original scalar loop) |
| pr358 | **6/7 — FAIL only on `no_subswath_info`**: 2665/3456 px (77.1%) differ; sub-swath digits dropped (e.g. oracle `0x004e000a`, got `0x004e0000`) |
| pr359 | 7/7 PASS |

Cause of the pr358 failure: `_get_sample_subswath_grid` loops
`for s in range(1, num_sub_swaths + 1)`; with `num_sub_swaths == 0`
the loop never runs and the result stays 0, while the scalar API
returns 1 for in-bounds samples
(`cxx/isce3/product/SubSwaths.cpp`, `getSampleSubSwath`: "If the
dataset does not have sub-swaths information, consider samples valid
and belonging to the first sub-swath"). One-line fix if that
implementation is kept.

### Reachability of `num_sub_swaths == 0`

The divergent input **cannot be produced by loading an RSLC file**:

- `Serialization.h` defaults `numberOfSubSwaths` to **1** when the
  dataset is absent, and leaves `validSamplesSubSwath1` empty when
  that dataset is absent — so a file without sub-swath information
  deserializes to `num_sub_swaths == 1` with an **empty array**, not
  to `num_sub_swaths == 0`.
- The `SubSwaths::numSubSwaths(n)` setter **throws** for `n <= 0`.
- `num_sub_swaths == 0` is reachable only programmatically: the
  pybind list constructor `SubSwaths(length, width, [])` accepts an
  empty vector (as this harness's fixtures do), and the C++ default
  constructor leaves the vector empty. The C++ scalar API explicitly
  defines the behavior for this state (the size-0 early return
  quoted above), so it is part of the documented API contract even
  though the file-driven InSAR workflow cannot hit it.

The `file_style_no_info` case (`num_sub_swaths == 1` + empty array —
what a file without sub-swath datasets actually loads as) **passes on
all three variants**, pr358 included: the practical "RSLC without
sub-swath info" scenario is handled correctly by both PRs, and the
pr358 divergence is a contract-fidelity gap for programmatically
constructed `SubSwaths` objects (library consumers, tests), not a
production-path bug.

## 2. Timing (`timing_<variant>.txt`)

`repro_insar_mask_timing.py` at the default production-like grid:
6840 x 10581 = 72.4 Mpx, three sub-swaths, synthetic offsets.
pristine x1 (it costs ~10 min per run), candidates x3.

| variant | wall time (s) | vs scalar | mask crc |
|---|---|---|---|
| pristine | 572.6 | 1x | `0x295ba19b` |
| pr358 | 14.7 / 15.8 / 16.5 (median 15.8) | ~36x | `0x295ba19b` (all runs) |
| pr359 | 9.7 / 10.0 / 10.8 (median 10.0) | ~57x | `0x295ba19b` (all runs) |

- CRC identical across every variant and run: on this (3-sub-swath)
  fixture all three implementations are output-identical.
- The pristine baseline is host-load sensitive: the #354 evidence
  bundle measured 383.3 s on a quieter host with the same ~57x ratio
  for pr359 (6.7 s), so ratios are stable even though absolute times
  moved.
- The ~1.6x between the two candidates is memory traffic: pr358
  materializes several full-grid float64/int64 temporaries
  (meshgrid + per-sub-swath boolean grids), pr359 processes row-wise.

## 3. Peak RSS (`rss_<variant>.txt`)

Same timing run wrapped with `resource.getrusage` (`ru_maxrss`),
one run per candidate:

| variant | peak RSS | wall time in this run |
|---|---|---|
| pr358 | 9869 MB (~9.6 GiB) | 14.8 s |
| pr359 | 1588 MB (~1.6 GiB) | 4.9 s |

The ~6.2x RSS gap matches the implementation difference (full-grid
temporaries vs row-wise). Wall times in this run are supplementary
(single run; pr359 landed on a quieter host window than its
median-of-3 above — the headline numbers remain the dedicated
timing runs).

## Files

- `crosscheck_{pristine,pr358,pr359}.txt` — oracle results
- `timing_{pristine,pr358,pr359}.txt` — full-grid wall times + CRC
- `rss_{pr358,pr359}.txt` — timing + `maxrss_mb` line
