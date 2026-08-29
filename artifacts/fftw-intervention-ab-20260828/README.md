# FFTW planner intervention A/B (bench#48)

Does replacing `FFTW_MEASURE` at the 7 pycuampcor planner sites actually
stop the 9-distinct-in-9-runs `dense_offsets` behaviour established in
bench#36 Step 2 — and at what wall-time cost?

- **Design + decision rules**: `PREREGISTRATION.md` (frozen before any
  result; deviations only via `AMENDMENT_*.md`).
- **Arm diffs**: `patches/` — applied as branches
  `expt/fftw-ab-{instr,armA,armB}` in the dedicated
  `isce3-v0.25.16` source checkout (base tag `v0.25.16` = `eed688e48`).
- **Harness**: `harness/` — `setup_base.sh`, `build_arm.sh`, `run_ab.sh`,
  `run_compare_ab.sh`; container-side `ab_inner.sh`.
  `compare_dof.py` / `wait_quiet.sh` / `configs/dof_rep.yaml` are
  byte-identical copies from the bench#36 Step 2 bundle
  (`../dense-offsets-fftw-planner-20260827/`).
- **Prior evidence**: bench PR #47 bundle (mechanism + 9-run background),
  report `reports/2026-08-cpu-insar-run-to-run-reproducibility.md`.

The upstream issue is HELD until this A/B completes (bench#48).
