#!/usr/bin/env bash
# Capture an Nsight Systems trace of a single GPU workflow run.
# Output: <log_dir>/nsys.nsys-rep  (open with `nsys-ui` on the host).
#
# Usage:
#   scripts/run_profile_nsys.sh [path/to/runconfig.yaml]
#   default config: configs/smoke_ree_rslc_gpu.yaml
set -euo pipefail
source "$(dirname "$0")/_common.sh"

cfg="${1:-${BENCH_ROOT}/configs/smoke_ree_rslc_gpu.yaml}"
if [ ! -f "${cfg}" ]; then
    echo "config not found: ${cfg}" >&2; exit 1
fi

if ! command -v nsys >/dev/null; then
    echo "nsys not installed in the container — install nsight-systems in the image" >&2
    exit 1
fi

run_dir="$(new_run_dir "nsys")"
record_provenance "${run_dir}"

wf="$(python -c "import yaml; print(yaml.safe_load(open('${cfg}'))['runconfig']['name'])")"

# -t cuda,nvtx,osrt: GPU + isce3 NVTX ranges + syscalls. -s cpu: CPU sampling.
# --cuda-memory-usage=true: track allocations.
nsys profile \
    --output "${run_dir}/nsys" \
    --trace cuda,nvtx,osrt \
    --sample cpu \
    --cuda-memory-usage true \
    --capture-range none \
    python -m "nisar.workflows.${wf}" "${cfg}" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "report: ${run_dir}/nsys.nsys-rep"
