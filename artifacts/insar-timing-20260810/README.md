# insar-timing-20260810 — GUNW run2 timing evidence (CPU vs GPU)

Timing evidence behind the NISAR insar (RSLC -> GUNW) CPU-vs-GPU report
([`reports/2026-08-insar-gpu-cpu-equivalence.md`](../../reports/2026-08-insar-gpu-cpu-equivalence.md),
issue [#26](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/26)).

## Provenance

- Runs: `gunw_cpu_run2` / `gunw_gpu_run2`, executed 2026-08-10 in the
  private `nisar-displacement` repo
  (`out/gunw_{cpu,gpu}_run2/` on the local NAS; `product.h5` at 1.6 GB
  each stays there).
- Pair: NISAR ASC 139/019, 2026-07-05 -> 2026-07-17 (Boso), freq A HH.
- isce3: v0.25.16 (upstream tag), from-source CUDA build.
- Host: nucbox-evo-t1 — RTX 5080 16 GiB (sm_120), 16-thread host,
  NVMe-backed out/scratch. The two runs differ **only** in
  `worker.gpu_enabled`.

## Files

| file | what |
|---|---|
| `insar_cpu_run2.log.gz` | full `insar.log` of the CPU run (gzip) |
| `insar_gpu_run2.log.gz` | full `insar.log` of the GPU run (gzip) |
| `timing_cpu_gpu2_flat_as_published.json` | flat per-stage timings as published with the 2026-08-10 report (no nesting info — summing it double-counts nested stages) |
| `timing_cpu_gpu2_tree.json` | regenerated with `tools/parse_insar_timing.py` bracket tracking: per-row parent, self time, timer-inclusiveness |
| `table_cpu_gpu2.md` | rendered tree table + accounting closure |
| `parser_warnings.txt` | structural warnings from the parser (the non-inclusive `Ionosphere` timer) |

## Regenerate

```bash
python3 tools/parse_insar_timing.py \
    artifacts/insar-timing-20260810/insar_cpu_run2.log.gz \
    artifacts/insar-timing-20260810/insar_gpu_run2.log.gz \
    --labels CPU GPU-5080 \
    --json artifacts/insar-timing-20260810/timing_cpu_gpu2_tree.json
```
