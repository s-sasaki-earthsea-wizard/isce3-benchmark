# bench#48 — FFTW planner intervention A/B — pre-registration (frozen before results)

Written 2026-08-28, **before** any arm was built or any replicate run. No
control, arm A, or arm B result existed at write time. Plan of record:
[bench#48](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/48)
as reviewed by the VECR team on 2026-08-28
(`.claude-notes/2026-08-28-fftw-ab-plan.md`), approved by Syota with base
pinned to **v0.25.16**.

The freeze marker is the git commit that introduces this file together with
`patches/` and `SHA256SUMS` on branch `feat/bench48-fftw-intervention-ab`.
Post-freeze changes go into `AMENDMENT_*.md` files in this directory, never
into this file.

## 1. Question

bench#36 Step 2 (PR #47) established mechanistically and correlationally
that CPU `dense_offsets` produces distinct results on byte-identical inputs
(9 distinct in 9 runs) and that the only nondeterministic ingredient is
`FFTW_MEASURE` planning without wisdom at the 7 planner sites in
`cxx/isce3/matchtemplate/pycuampcor/`. This A/B closes the causal loop **by
intervention**:

1. Does replacing the planner policy actually stop the run-to-run
   divergence — both the 1/32 px branches and the ~60 px excursions?
2. What does the replacement cost in `dense_offsets` wall time, given that
   plan construction is fully amortised (plans built once per process,
   executed for 545 chunk rows) — i.e. the best case for `FFTW_MEASURE`?

## 2. Fixed environment

- Host: nucbox-evo-t1, same host as all bench#36 Step 2 runs. Host
  quiescence gate before every replicate: `wait_quiet.sh 8 3 600` (3
  consecutive 5 s samples below 8% CPU).
- Threading env inside the container: `OMP_NUM_THREADS=16`,
  `MKL_NUM_THREADS=16`, `OPENBLAS_NUM_THREADS=16`, no synthetic load
  (the Step 2 "idle" protocol).
- Source tree: the dedicated checkout
  `/mnt/nas/Projects/third-party-projects/isce3-v0.25.16`, base tag
  **v0.25.16 = `eed688e48`** (Syota's call 2026-08-28: reproducible for
  maintainers, no dependence on unrelated `develop` movement).
- Build tree: `isce3-benchmark/isce3-build-v0.25.16` (persistent CMake
  cache; each arm is an incremental rebuild — 2 translation units plus
  relink — via `scripts/build_isce3.sh`, which includes the mandatory
  patchelf RPATH rewrite).
- Input: the coarse-resampled secondary from the bench#36 phase0 scratch,
  mounted read-only, so inputs are byte-identical across all replicates of
  all arms **by construction**. Runconfig `configs/dof_rep.yaml` is a
  byte-identical copy of the Step 2 one (sha256
  `26dca24302dab762410c086d83c49b19516d542d4e76baf373e20e23edc77e16`).
- The per-rep `reference.slc` regeneration was proven deterministic in
  Step 2; each rep records its sha256, then deletes the 17 GB file.

## 3. Arms

Experiment branches in the dedicated source tree, all committed before any
build. Diffs are frozen as `patches/*.patch` (hashes in `SHA256SUMS`).

| arm | branch @ SHA | change on top of v0.25.16 | n |
|---|---|---|---|
| **control** | `expt/fftw-ab-instr` @ `026787b00` | observation probe only; all 7 sites stay `FFTW_MEASURE` | 3 |
| **A** | `expt/fftw-ab-armA` @ `e9390e9b2` | probe + all 7 sites → `FFTW_ESTIMATE` | 3 |
| **B** | `expt/fftw-ab-armB` @ `d63c470a2` | probe + `planFlags()`: wisdom import from a fixed path, then `FFTW_MEASURE \| FFTW_WISDOM_ONLY`, NULL plan → throw (fail closed) | 3 |

Arm A is the candidate 7-line upstream fix. Arm B is a **PoC, not a product
implementation** — it answers "what does pinning cost/buy", not "how should
upstream ship wisdom".

### Deviation D-1 from the bench#48 matrix, declared at freeze

bench#48 wrote "control: no change". Outcome measures 1 and 5 (plan
construction time, plan hash) are unobservable without in-process
instrumentation, so the control carries the same observation probe as the
arms (`patches/00-common-instrumentation.patch`,
`cuFftwPlanProbe.h` + 7 call-site wrappings). The probe is env-gated,
byte-identical across all three arms, and adds only: two `steady_clock`
reads per planner call, optional stderr prints, optional wisdom export
after planning, and a NULL-plan check that `FFTW_MEASURE`/`FFTW_ESTIMATE`
planning cannot trigger. The A/B contract "arms differ only in planner
flags" is preserved exactly.

### Runtime plan-call count

The 7 planner sites are source-level. `cuAmpcorChunk`'s constructor
instantiates `cuFreqCorrelator` twice (raw + oversampled),
`cuOverSamplerC2C` twice, and `cuOverSamplerR2R` once on this config path,
so up to 12 planner calls are expected per process. The probe log counts
them empirically; the prediction is recorded as P6 below.

## 4. Procedure (execution order)

1. `setup_base.sh` — seed `~/scratch/fftw_ab_20260828` (configs, inner
   script, comparators). Record container image id in the first build log.
2. `build_arm.sh expt/fftw-ab-instr ctrl` → `run_ab.sh ctrl 3`
   (reps `ctrl_1..3`).
3. `build_arm.sh expt/fftw-ab-armA armA` → `run_ab.sh armA 3`.
4. `build_arm.sh expt/fftw-ab-armB armB` →
   `WISDOM_GEN=1 run_ab.sh wisdomgen 1` (generator run: exports
   `armB_wisdom.f`; its Ampcor outputs are **excluded** from all
   comparisons) → `ARMB_WISDOM=1 run_ab.sh armB 3`.
5. `run_compare_ab.sh ctrl armA armB` + wall-time extraction from
   `/usr/bin/time -v` (`Elapsed (wall clock)`).
6. Extend the **selected** arm (per §6) to n=5: re-run `build_arm.sh` for
   its ref (the shared build tree will have moved), then
   `run_ab.sh <tag> 2 4`.

Per replicate the harness records: `run.log` / `run.err` (with
`[pycuampcor-fftw]` planner lines), `/usr/bin/time -v` block, per-rep
`plan_wisdom.f`, arm source SHA + porcelain, `reference.slc` sha256.
Per build: source SHA, diff-stat vs v0.25.16, build log, `libisce3.so`
sha256.

## 5. Outcome measures

1. **Plan construction time**: per-site planner seconds from the probe;
   per-run total = their sum.
2. **`dense_offsets` total wall** = `Elapsed (wall clock)` of the workflow
   process — the primary cost metric (it contains (1); (1) is also
   reported separately for mechanism attribution).
3. **sha256 of all 5 Ampcor layers** per rep (`dense_offsets`,
   `gross_offsets`, `snr`, `covariance`, `correlation_peak`) → within-arm
   distinct counts (`compare_dof.py`).
4. **Cross-arm differences** vs `ctrl_1` for `dense_offsets`: max |Δ| and
   quantiles.
5. **Plan hash** = sha256 of the run's exported `plan_wisdom.f`. Arm B
   additionally: byte equality of per-rep exports with each other and
   with the generator's `armB_wisdom.f`.

## 6. Decision rules (frozen)

- **Truth gate**: an arm passes iff all its replicates are byte-identical
  across **all five** output layers.
- **Positive-control check**: the control is expected to FAIL the truth
  gate at n=3 (background: 9 distinct in 9 runs on the previous build).
  If it unexpectedly passes, extend control to n=5 **before** interpreting
  any arm; if it still passes, the A/B is inconclusive against background
  and stops with no recommendation (the n=3-agreement trap from Step 2 is
  exactly this scenario).
- **Scale separation on failure**: if any arm fails the truth gate,
  classify its within-arm diffs by scale before interpreting — 1/32 px
  lattice (oversampled stage) vs ≳1 px excursions (raw-correlation
  stage). One can vanish while the other persists; report them
  separately.
- **Cost gate**: if arm A passes the truth gate AND its median total wall
  (measure 2) is ≤ 1.05 × control median → **recommend A** (operationally
  far simpler). Medians at n=3 decide provisionally; the n=5 extension of
  the selected arm re-evaluates the gate, and **n=5 governs** on
  disagreement.
- If arm A passes the truth gate but exceeds +5%: arm B becomes the lead
  candidate if it passes its truth gate; A's measured cost is published
  regardless.
- **Arm B NULL-plan abort** (no applicable wisdom — e.g. alignment-class
  mismatch between generator and rep) is an **informative failure**:
  reported as "WISDOM_ONLY pinning is not viable without alignment
  guarantees", not iterated on inside this A/B.
- The comparison uses only reps run under this pre-registration. The nine
  Step 2 runs are background evidence (different build), not a control.

## 7. Predictions (recorded before results)

- **P1**: control fails the truth gate at n=3.
- **P2**: arm A passes the truth gate, and its total plan-construction
  time is far below control's.
- **P3**: arm A's total-wall delta vs control is the genuine unknown of
  this experiment — no direction is predicted. (VECR review consensus:
  "ESTIMATE is probably free" is *not* a safe assumption here, because
  plan cost is amortised and plan quality fully realised.)
- **P4**: arm B passes the truth gate; its per-rep plan hashes are
  identical to each other and to the generator wisdom's.
- **P5**: probe overhead is negligible (« 1 s against ≥ 500 s walls).
- **P6**: the probe logs 12 planner calls per run (7 sites × instance
  counts: 2× cuFreqCorrelator, 2× C2C, 1× R2R).

## 8. Cost budget

10 measured runs (3+3+3 + 1 generator) + up to 2 extension reps, at
519–703 s each (Step 2 idle/omp1 range), plus 3 incremental builds ≈
**1.7–2.3 h** total.

## 9. Exit

Rewrite the held upstream issue draft
(`.claude-notes/2026-08-27-fftw-issue-body-upstream.md`) as **"Evaluated
options + Recommendation"** with the measured numbers, applying the four
review corrections listed in bench#48. Whether an implementation PR
accompanies it is decided **after** the numbers exist. Nothing is posted
upstream without Syota's explicit sign-off.
