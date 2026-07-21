# RTX 5080 evidence bundle for the geocode_slc CUDA RFC (2026-07-20/21)

Frozen copies of the consumer-GPU (RTX 5080) measurement runs cited by
the upstream RFC "CUDA backend for geocodeSlc". The A100 scale-up
evidence lives separately in `artifacts/a100-subswath-20260720/`.

Host for all runs: Intel Core Ultra 9 285H (16 cores), 96 GB RAM,
NVIDIA GeForce RTX 5080 16 GiB (driver 590.48.01, CUDA 12.8), Ubuntu
24.04 dev container. Full per-run details in each `provenance.txt`.

## Runs

| dir | what it evidences | isce3 build SHA | harness @ bench SHA |
|---|---|---|---|
| `20260720T115734Z_pyspy_geo/` | py-spy `--native` flamegraph, parallel run (76.5 % geo2rdr) + gzip A/B compression log | `d9e2d67` (fork) | `run_profile_pyspy.sh` @ `154752f` |
| `20260720T123151Z_pyspy_geo/` | py-spy `--native`, `OMP_NUM_THREADS=1` (69 % geo2rdr) | `d9e2d67` (fork) | `run_profile_pyspy.sh` @ `154752f` |
| `20260720T125127Z_perf_geo/` | `perf record` cycles:u all-thread (~77 % geo2rdr; `Orbit::interpolate` ≈ 54 % of user cycles) + `perf stat` (IPC ≈ 4.0). **No provenance.txt was captured for this run**; it is the same session, build, and host as the two py-spy runs above. `perf.data` (1.8 MB binary, host-specific) is excluded; the text reports carry the evidence. | `d9e2d67` (fork, inferred — see note) | ad-hoc `perf record`/`perf stat` in the dev container (perf compose override, bench PR #13) @ `154752f` |
| `20260720T131432Z_trial_cuda_geocode/` | e2e trial n=5: GPU 2.95 s vs CPU parity 15.04 s (5.1×), mask/index/amplitude agreement, VRAM sampling | `d9e2d67` (fork) | `trial_cuda_geocode_e2e.py` @ `154752f` |
| `20260720T133244Z_trial_cuda_geocode/` | lines_per_block 200→100 bisect (4-px block-invariance diff) | `d9e2d67` (fork) | `trial_cuda_geocode_e2e.py` @ `154752f` |
| `20260721T171742Z_trial_cuda_geocode/` | #270-independence verification: same trial (n=3) on **unpatched upstream develop**, PASS, 4.84× — see `artifacts/verify_rfc2_geocode_trial_without_270.txt` | `2919e1c9` (upstream develop) | `trial_cuda_geocode_e2e.py` (unchanged since `154752f`) |
| `poc_geocode_slc_20260720T105732Z/` | standalone fp32/fp64 flattening-kernel study (fp32 ~10 rad phase error; fp64 5.5× slower than fp32, far from 64× nominal). Synthetic 1046×645 grid — NOT burst-scale. | n/a (standalone .cu) | `poc/geocode_slc/` @ `dc2f12a` |

## SHA legend

- isce3 fork `d9e2d67` = upstream develop `2919e1c9` + the
  [#270](https://github.com/isce-framework/isce3/pull/270) fix
  (rdr2geo bindings only; verified irrelevant to these geocode paths —
  see the `20260721T171742Z` run).
- Bench SHAs refer to this repository's `main` history.

## Caveats

- All timing numbers are hardware- and workload-specific (single S1
  IW3 burst, ~67.5 Mpx geogrid). Do not compare across bundles without
  the hardware label: RTX 5.1× (n=5, `d9e2d67` build) / 4.84× (n=3,
  `2919e1c9` build, different day) vs A100 6.7× are different hosts,
  builds, and run counts.
- `run.err` files that were empty at capture time are omitted.
