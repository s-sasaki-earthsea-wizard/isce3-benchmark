#!/usr/bin/env bash
# Capture an Nsight Systems trace of a single workflow run.
# Output: <log_dir>/nsys.nsys-rep  (open with `nsys-ui` on the host).
#
# Usage:
#   scripts/run_profile_nsys.sh [path/to/runconfig.yaml] [compass_grid]
#   default config: configs/smoke_ree_rslc_gpu.yaml
#   compass_grid:   radar (default) or geo — used only when the config is a
#                   COMPASS CSLC runconfig. Geo mode targets geocode_slc.
set -euo pipefail
source "$(dirname "$0")/_common.sh"

cfg="${1:-${BENCH_ROOT}/configs/smoke_ree_rslc_gpu.yaml}"
compass_grid="${2:-radar}"
if [ ! -f "${cfg}" ]; then
    echo "config not found: ${cfg}" >&2; exit 1
fi

if ! command -v nsys >/dev/null; then
    echo "nsys not installed in the container — install nsight-systems in the image" >&2
    exit 1
fi

ensure_runconfig_paths "${cfg}"
mapfile -t cmd < <(dispatch_workflow "${cfg}" "${compass_grid}")

tag="nsys"
if [ "${compass_grid}" = "geo" ]; then tag="nsys_geo"; fi
run_dir="$(new_run_dir "${tag}")"
record_provenance "${run_dir}"

# -t cuda,nvtx,osrt: GPU + isce3 NVTX ranges + syscalls. -s cpu: CPU sampling
# (degraded to off-by-default when host kernel.perf_event_paranoid >= 3 —
# in that case only CUDA + OSRT + NVTX are recorded).
# --cuda-memory-usage: track allocations.
nsys profile \
    --output "${run_dir}/nsys" \
    --trace cuda,nvtx,osrt \
    --sample cpu \
    --cuda-memory-usage true \
    --capture-range none \
    "${cmd[@]}" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "report: ${run_dir}/nsys.nsys-rep"
