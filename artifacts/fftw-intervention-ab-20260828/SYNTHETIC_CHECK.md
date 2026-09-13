# Synthetic run-to-run reproducibility check — development runs (2026-09-13)

`scripts/repro_cpu_ampcor_determinism.py` is a self-contained check for
CPU Ampcor (`isce3.matchtemplate.PyCPUAmpcor`): no granules, seconds per
run. It generates a band-limited complex noise reference and a secondary
that is the same field shifted by a known sub-pixel amount (exact Fourier
shift, +5 % independent noise), runs `PyCPUAmpcor` N times in **separate
processes** on the byte-identical inputs, hashes the five output rasters,
and checks the recovered shift. It stores no golden hashes — runs are
compared with each other only — so it does not depend on the host
toolchain. Window geometry is the NISAR `dense_offsets` production
configuration (window 64x96, half-search 32/32, skip 75, SLC oversampling
2, statistics 21, zoom 8, surface oversampling 16 by FFT, batch 10x1,
frequency-domain correlation), so all seven planner sites fire — 12
planner calls per process, confirmed by the probe on every run below.

These are **development runs by 華扇** (the script's author) on the arm
binaries of the 2026-08-28 A/B, to establish that the check behaves as
designed. The evidence-of-record runs are Syota's (see the disclosure in
the upstream draft).

| run | binary | mode | distinct / 5 runs (dense_offsets, gross_offset, snr, covariance, correlation_peak) | wall per run | result |
|---|---|---|---|---|---|
| A | arm B `d63c470a2`, **no env = stock `FFTW_MEASURE`** | `--expect-identical` | 1, 1, **2**, **2**, **5** | 0.59–0.69 s | **FAIL** (exit 1), as intended for stock |
| B | arm B `d63c470a2`, `--wisdom-export` → `--wisdom-import` | `--expect-identical --expect-wisdom-enforcement` | 1, 1, 1, 1, 1 | 0.10–0.12 s | **PASS**; generator 11,736 B wisdom; bogus wisdom path → rc=1 (fail closed) |
| C | arm A `e9390e9b2` (`FFTW_ESTIMATE`), no env | `--expect-identical` | 1, 1, 1, 1, 1 | 0.13–0.24 s | **PASS** |

Accuracy in all three: median recovered offset az −0.3750 / rg +0.2500 px
= the known shift exactly, max |err| 0, 100 % of windows within 0.0625 px;
median snr 748, median correlation peak 0.972. Inputs identical across the
three runs (reference sha256 `357fecb0…`, secondary `de413fce…`).

Reading:

- On synthetic high-SNR data the stock policy diverges on the
  ULP-sensitive layers (`correlation_peak`, `snr`, `covariance`) while
  `dense_offsets` stays identical: the 1/32 px quantisation absorbs
  roundoff there. Real data adds the between-group argmax flips seen in
  the 9-run background; the check does not try to reproduce those.
- The stock-policy failure is a demonstration on this host, not a
  guarantee: a very quiet host can make `FFTW_MEASURE` converge on one
  plan. The check is a property test for a fixed planner policy.
- Arm B's run time drops from ~0.6 s to ~0.1 s because the stock run
  spends ~0.5 s in `FFTW_MEASURE` timing (12 planner calls); on the real
  step that cost is amortised and invisible.
- `gross_offset` is a constant 0 (gross offsets disabled) and is listed
  for completeness. (`dense_offsets.py` pre-creates `gross_offsets`,
  plural, and Ampcor writes `gross_offset`, singular; both are all-zero.
  The check uses the singular name for create and write.)

Reproduce (container, worktree or checkout mounted at `/work`):

```
python3 scripts/repro_cpu_ampcor_determinism.py --workdir /tmp/det --runs 5 --expect-identical
python3 scripts/repro_cpu_ampcor_determinism.py --workdir /tmp/det --runs 5 \
    --wisdom-export /tmp/det/wisdom.f --wisdom-import /tmp/det/wisdom.f \
    --expect-identical --expect-wisdom-enforcement
```

Raw outputs: `logs/synthetic_check_{stock,wisdom,estimate}.txt`.
