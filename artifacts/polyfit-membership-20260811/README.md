# Polyfit quantum-membership amplification — evidence (2026-08-11)

Public home of the reproduction assets for the finding tracked in
[issue #26](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/26):
the CPU-vs-GPU difference of the NISAR RIFG `pixelOffsets` layer
(a smooth ~3.6e-2 px RMS degree-2 surface) is caused by a **single
one-quantum (1/32 px) subpixel-peak disagreement in one
high-correlation Ampcor window**, amplified discontinuously by the
sequential worst-outlier removal of the production rubbersheet fit
(`isce3.math.offsets_polyfit.polyfit_offsets`, default
`crit_value=0.1`). The amplifier is an endgame *membership* effect:
the removal chain forks near its end and the final inlier elite —
the set that defines the fit — changes wholesale (1,677 vs 1,535
survivors, 985 common, on the real 40k sample set).

The investigation ran in a private working repo; the artifacts here
are verbatim copies of its recorded results plus fresh verification
runs executed in this repository (see "Verification runs" below).
The upstream-facing issue will link this directory.

## Mechanism in one inequality

At a high-quality window (corr_peak w = 0.9485) the w-test stop
tolerance of the production fit is

    crit_value * sigmaL / w = 0.1 * 0.1247 / 0.9485 = 0.0132 px

which is **below the input quantization floor** q/2 = 1/64 =
0.0156 px of the Ampcor correlation grid. The purge therefore digs
into the high-weight elite (95.8% of the 40k production samples are
removed) and the final membership sits on a knife edge: a one-quantum
input flip can re-route the endgame chain. The response is
piecewise-constant — most perturbations are benign, but a flip that
crosses an endgame membership boundary jumps the fitted surface by
orders of magnitude more than any continuous response
(3.6e-2 px vs ~1e-5 px scale on the real data).

## Files

Real-40k replay (L1, exact reproduction of the production fits):

- `replay_real40k.json` — gate, legs, factorial, microscope and
  single-node hybrid probes. Headline: swapping only the raw
  dense-offsets input reproduces the observed CPU-minus-GPU
  coefficient difference with cosine 1-6e-15 (residual induced field
  9e-9 px vs the 3.6e-2 px target); the minimal destructive set is
  ONE node (sample row 22961, corr_peak 0.9485, dAz exactly
  -1/32 px), necessary and sufficient.
- `replay_gate_omp16.json` — cross-thread determinism control
  (OMP=1 vs 16 bit-identical).
- `replay_real40k_quicklook.png` — amplification curve (the chains
  differ by 1e-5..1e-3 px mid-run and explode over the last ~2,000
  iterations) and the replayed degree-2 azimuth difference surface.
- `probe_rusudan.json`, `probe_necessity_crit.json` — follow-up
  probes: necessity closure (complement transplants = exactly zero),
  weight-channel exclusion (peak-only 1.9e-12 px), the perturbation
  profile (only the exact -1/32 px CPU value lands in the target
  basin; -2/32, +1/32, +2/32, -1e-4, -1e-5 are all benign) and the
  crit_value sweep (the amplification is specific to the default
  0.1: at 0.2 the flip response drops 160-fold).

Minimal synthetic reproducer (L0, public, self-contained):

- `minrepro_synthetic900.json` — the pinned existence proof
  (seed 29, 30x30 = 900 samples, production kwargs): baseline
  removes 868/900 (retention 3.6%) with the driver surviving; the
  -1/32 px flip removes it at 95.6% of the chain, replaces 11
  further members of the final elite (32 vs 30 inliers, 25 common)
  and jumps the fitted azimuth surface by RMS 2.75e-2 px.
- `minrepro_hunt40.json` — the calibration hunt over seeds 0-39
  (22 baseline-qualify, 7 pass all flip criteria). The pinned seed
  is an existence proof; no prevalence claim is made.

Verification runs in this repository (fresh, not copied):

- `repro_run_host.txt` / `repro_run_host.json` — host run of
  `scripts/repro_polyfit_quantum_membership.py` (file import from
  the surrounding isce3 checkout, numpy 2.2.6): 7/7 checks PASS.
- `repro_run_container.txt` / `repro_run_container.json` — the same
  inside the `isce3-benchmark:dev` container (isce3
  0.26.0-dev+2919e1c97 from-source build, numpy 1.26.4): 7/7 PASS.
  The discrete outcome (removal counts, driver removal iteration,
  fork iteration, final membership) is **identical** across the two
  environments; only the float tails of the coefficients differ
  (~2 ULP, numpy/BLAS version difference).

## Reproducing

The synthetic reproducer needs nothing but isce3 (or its source
tree) and numpy:

```
# host (imports the surrounding checkout when isce3 is not installed)
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
python3 scripts/repro_polyfit_quantum_membership.py

# dev container
docker compose run --rm dev \
    python3 scripts/repro_polyfit_quantum_membership.py
```

`scripts/polyfit_sensitivity.py` is the full harness the numbers
were produced with (subcommands: `pure`, `replay`, `minrepro`,
`probe`); its `minrepro` subcommand produces bit-identical results
to the standalone script (verified across both repos and both
environments).

The `replay` and `probe` subcommands need the NISAR-derived local
inputs (raw dense-offsets rasters of the three Ampcor runs, RIFG/
RSLC HDF5, production insar.log files) which are **not** included
here — they live on the measurement host, and the per-run input
md5s are recorded inside `replay_real40k.json`. The 14.5 MB npz
with the extracted 40k sample matrices is also withheld for now
pending a redistribution check of NISAR-derived data.

## Scope and limitations

- Single dataset, single pair (NISAR L-SAR ASC 139/019, CPU vs GPU
  InSAR workflows); the replay determinism claims are same-
  environment claims.
- The synthetic case is an existence proof at 900 samples, found by
  a documented 40-seed hunt; prevalence would need a separate seed
  ensemble.
- The origin of the one-quantum Ampcor argmax flip itself (why the
  CPU and GPU correlation surfaces rank neighboring quanta
  differently in that window) is not part of this evidence set.
