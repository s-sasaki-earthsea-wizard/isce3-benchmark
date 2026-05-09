# Benchmark runconfigs

Each scenario is a **pair** of YAML runconfigs that differ only in
`worker.gpu_enabled` (and `worker.gpu_id` if multi-GPU). Naming convention:

```
<scenario>_<cpu|gpu>.yaml
```

`scripts/run_bench.sh` discovers scenarios by listing `<scenario>_cpu.yaml` and
running both halves of the pair.

## Filling in paths

The runconfigs reference input files via absolute container paths. Inside the
container, the bind mounts are:

| Container path | Host source        |
|----------------|--------------------|
| `/data`        | `${BENCH_DATA_DIR}` |
| `/logs`        | `${BENCH_LOG_DIR}`  |
| `/opt/isce3-src` | `${ISCE3_SRC}` (read-only) |

So a REE product staged into `./data/REE/REE_L0B_out17.h5` is referenced as
`/data/REE/REE_L0B_out17.h5` inside a runconfig.

## Templates

`smoke_ree_rslc_cpu.yaml` / `smoke_ree_rslc_gpu.yaml` will be added once the
REE staging step (`make data-ree`) is validated and we confirm the actual
filenames present in the test fixture set on this isce3 commit.

`micro/` holds single-stage runconfigs for upstream RFC repros: each one
exercises exactly one GPU-accelerated stage (e.g. crossmul-only, geo2rdr-only)
to keep the bug surface small.
