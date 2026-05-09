#!/usr/bin/env bash
# py-spy sampling profile of a single workflow run. Captures CPU-side hotspots
# (Python frames + native frames) — complementary to Nsight which sees GPU.
#
# Usage:
#   scripts/run_profile_pyspy.sh [path/to/runconfig.yaml]
set -euo pipefail
source "$(dirname "$0")/_common.sh"

cfg="${1:-${BENCH_ROOT}/configs/smoke_ree_rslc_cpu.yaml}"
if [ ! -f "${cfg}" ]; then
    echo "config not found: ${cfg}" >&2; exit 1
fi

run_dir="$(new_run_dir "pyspy")"
record_provenance "${run_dir}"

wf="$(python -c "import yaml; print(yaml.safe_load(open('${cfg}'))['runconfig']['name'])")"

py-spy record \
    --output "${run_dir}/pyspy.svg" \
    --format flamegraph \
    --rate 100 \
    --subprocesses \
    --native \
    -- python -m "nisar.workflows.${wf}" "${cfg}" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "flamegraph: ${run_dir}/pyspy.svg"
