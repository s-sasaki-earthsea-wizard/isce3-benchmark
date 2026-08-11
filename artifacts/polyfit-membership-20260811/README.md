# Polyfit quantum-membership amplification — evidence (2026-08-11)

Public home of the reproduction assets for the finding tracked in
[issue #26](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/26):
the CPU-vs-GPU difference of the NISAR RIFG `pixelOffsets` layer is
a smooth ~3.6e-2 px RMS degree-2 surface, and in a **controlled
replay** of the production rubbersheet fit
(`isce3.math.offsets_polyfit.polyfit_offsets`, default
`crit_value=0.1`), transplanting **one isolated CPU-Ampcor sample —
a high-correlation window whose subpixel peak moved by exactly one
quantum (1/32 px) — into the GPU baseline is necessary and
sufficient** to reproduce the observed coefficient difference to
the available production log precision. The amplifier is an endgame
*membership* effect of the sequential worst-outlier removal: the
chain forks near its end and the final inlier elite — the set that
defines the fit — changes wholesale (1,677 vs 1,535 survivors, 985
common, on the real 40k sample set). The actual production CPU raw
offsets were not reconstructed, so actual-run node provenance
remains a recorded reservation (see Scope).

The investigation ran in a private working repo; the artifacts here
are verbatim copies of its recorded results plus fresh verification
runs executed in this repository (see "Verification runs" below).
The upstream-facing issue will link this directory.

## Mechanism in one inequality

The exact w-test stop tolerance at a sample is
`crit_value * sigma * sqrt(1/w^2 - h_ii)`; ignoring the leverage
term gives the upper bound `crit_value * sigma / w`. At the driver
window's quality (corr_peak w = 0.9485) the bound is

    crit_value * sigmaL / w = 0.1 * 0.1247 / 0.9485 = 0.0132 px

which sits **below the half-bin bound q/2 = 1/64 = 0.0156 px** on
the nearest-grid quantization error of the Ampcor correlation grid
— such a sample can fail the stop test on quantization error alone.
This is a high-weight-tail property, not an elite-wide one: at the
survivor-median weight 0.56 the bound is 0.0223 px and the
inequality reverses. The observed production purge (95.8% of the
40k samples removed; the fit decided by a ~4% elite) shows the
susceptibility was realized on this data, leaving the final
membership on a knife edge. The fit response to an input offset is
piecewise-smooth — linear in the offsets while the removal chain is
unchanged, and exactly constant in a perturbed sample's value once
that sample is purged — with discontinuous jumps at membership
boundaries; across such a boundary the jump is neither proportional
nor monotone in the perturbation (3.6e-2 px from one quantum here,
vs a ~1e-5 px continuous-response scale).

## Files

Real-40k replay (L1, controlled replay of the production fits):

- `replay_real40k.json` — gate, legs, factorial, microscope and
  single-node hybrid probes. Headline: swapping only the raw
  dense-offsets input reproduces the observed CPU-minus-GPU
  coefficient difference with cosine 1-6e-15 (residual induced field
  9e-9 px vs the 3.6e-2 px target); the minimal destructive set is
  ONE node (sample row 22961, corr_peak 0.9485, dAz exactly
  -1/32 px), necessary and sufficient within the observed
  difference sets.
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
  the surrounding isce3 checkout, numpy 2.2.6): 8/8 checks PASS.
- `repro_run_container.txt` / `repro_run_container.json` — the same
  inside the `isce3-benchmark:dev` container (isce3
  0.26.0-dev+2919e1c97 from-source build, numpy 1.26.4): 8/8 PASS.
  The discrete outcome (removal counts, driver removal iteration,
  fork iteration, final membership) was **identical in the two
  tested software environments on the same host**; coefficient
  tails differed by up to ~2 ULP (numpy/BLAS version difference).
  Other CPU architectures / BLAS implementations are untested.

## Reproducing

The synthetic reproducer runs in any isce3 Python environment
(NumPy, and SciPy transitively via the module under test):

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

**Reproducibility split**: the synthetic L0 is fully reproducible
from this repository alone. The real-40k L1 results are **recorded
evidence** — the `replay` and `probe` subcommands need the
NISAR-derived local inputs (raw dense-offsets rasters of the three
Ampcor runs, RIFG/RSLC HDF5, production insar.log files), which are
not included here, so a third party cannot independently replay
them without those rasters or the extracted-sample npz. Provenance
remains recorded: per-run input md5s live inside
`replay_real40k.json`, and the withheld npz (14.5 MB, NISAR-derived
40k sample matrices; redistribution decision pending) is pinned as

    replay_real40k.npz  SHA-256
    000401caabc1fefedac6111d972773cf0f06c76ec3c8dc34fc2301c21a15c2c5

with 22 arrays (`samples_{G,A,B}`, `removed_*`, `stop_margin_*`,
`selection_margin_*`, `coef_log_{L,P}_*`, `amp_curve_*`) produced
deterministically by the harness `replay` subcommand.

## Scope and limitations

- Single dataset, single pair (NISAR L-SAR ASC 139/019, CPU vs GPU
  InSAR workflows); the replay determinism claims are same-
  environment claims.
- **Actual-run reservation (L2a)**: the L1 evidence is a controlled
  substitution on recorded rasters; the actual production CPU run's
  raw offsets were not reconstructed, so "the one flip existed in
  the actual CPU run" is inferred (strongly — the replayed
  coefficients land on the CPU run's logged values to 5e-8) but not
  directly observed.
- The synthetic case is an existence proof at 900 samples under
  production kwargs, found by a documented 40-seed hunt; its
  two-population weight structure is a simplification of the
  production corr_peak distribution, not a quantitative match
  (qualitatively consistent: real survivor-median peak 0.56 falls
  inside the synthetic coherent range 0.3-0.8).
- The origin of the one-quantum Ampcor argmax flip itself (why the
  CPU and GPU correlation surfaces rank neighboring quanta
  differently in that one window) is out of scope here.
- Most perturbations are benign: 11 of the 12 real flipped nodes
  and all 38.5k weight epsilons re-converged harmlessly; the
  mechanism requires crossing an endgame membership boundary.
