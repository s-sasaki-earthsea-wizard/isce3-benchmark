# A100 full-subswath / full-frame CUDA Geocode trial — key artifacts

Runpod pod, 2026-07-20. NVIDIA A100-SXM4-80GB (driver 580.126.16,
CUDA 12.4, sm_80), 2× AMD EPYC 7742 (128 cores, **host-shared** —
CPU numbers carry a shared-host asterisk), 2 TB RAM. isce3 built
from source at fork SHA `d9e2d67` (same as all prior measurements),
gcc 13.4 (conda-forge), Python 3.12. Harness: bench branch
`feat/a100-subswath-e2e`, `scripts/trial_cuda_geocode_subswath.py`.
Full run dirs (untracked): `logs_runpod-a100/`.

## Files

- `results_iw2_subswath.json` + `provenance_iw2_subswath.txt` — all 9
  IW2 VV bursts of the Boso reference SAFE
  (S1A_IW_SLC 20251221T204341), repeats=3, CPU+GPU+validation+
  lines_per_block scan. Geogrids ~3480×23300 each, 734 Mpx total.
- `results_frame.json` + `provenance_frame.txt` — all 27 VV bursts
  (IW1+IW2+IW3, 2.04 Gpx total), GPU batch amortization only
  (`--skip-cpu`), repeats=1.
- `vram_probe_results.jsonl` — out-of-process ~58 kHz pynvml sampling
  of one CUDA Geocode call per lines_per_block
  (`scripts/probe_vram_transient.py`), burst t046_097520_iw2.

## Headline numbers

IW2 subswath (9 bursts, same-host CPU comparison, lpb=200):

| metric | value |
|---|---|
| GPU sum of warm medians | 23.79 s |
| GPU batch wall (cold incl.) | 27.73 s |
| CPU parity sum of medians | 159.19 s |
| CPU prod sum of medians | 166.11 s |
| speedup (parity, sum) | **6.69×** |
| speedup (parity vs batch wall) | **5.74×** |

Validation across all 9 bursts: valid masks identical (Jaccard 1.0),
rg index max |Δ| ≤ 0.0195 px, az ≤ 0.0013 px, amplitude r ≈ 1.0
(worst rel_amp_err_max 4.3e-2, same chip-edge character as RTX).

Full frame: 27 bursts / 2.04 Gpx in **62.0 s** batch wall (cold first
call 2.98 s; per-burst 0.78–2.98 s).

lines_per_block scan (in-run 20 ms sampler UNDERSTATES peaks; trust
the probe):

| lpb | wall (s) | probe peak (MiB) | chip model (GiB) | >4 GiB dwell (s) |
|---|---|---|---|---|
| 200 | 2.37 | 4,849 | 2.84 | 0.20 |
| 1000 | 1.59 | 16,951 | 14.19 | 0.044 |
| 2000 | 1.49 | 32,049 | 28.39 | 0.21 |

Baseline-subtracted peak scaling is consistent with the sinc
chip-buffer transient model (n_elem_out × 81 × 8 B; baseline
1,565 MiB) — and the transient is so short-lived (~tens of ms) that
20–50 ms samplers can miss or severely understate it. lpb=1000 on
this geogrid materializes a measured 16,951 MiB allocation peak,
which exceeds an RTX 5080's total 16.3 GiB VRAM, so the consumer-card
OOM hazard is evidence-backed; no OOM was provoked on the RTX itself.
Bit-exact output across lpb ∈ {100,200,500,1000,2000} on this burst
(n_differing_px = 0 vs lpb=200 reference).
