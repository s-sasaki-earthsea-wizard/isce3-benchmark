#!/usr/bin/env bash
# End-to-end trial of the existing isce3.cuda.geocode.Geocode vs the CPU
# isce3.geocode.geocode_slc on the real Boso burst. See
# scripts/trial_cuda_geocode_e2e.py for design and metrics.
#
# Usage:
#   scripts/run_trial_cuda_geocode.sh [runconfig.yaml] [repeats] [lines_per_block]
set -euo pipefail
source "$(dirname "$0")/_common.sh"

cfg="${1:-${BENCH_ROOT}/configs/insar_s1_boso_geo_cpu.yaml}"
repeats="${2:-5}"
lines_per_block="${3:-200}"
if [ ! -f "${cfg}" ]; then
    echo "config not found: ${cfg}" >&2; exit 1
fi

ensure_runconfig_paths "${cfg}"

run_dir="$(new_run_dir trial_cuda_geocode)"
record_provenance "${run_dir}"

python "${BENCH_ROOT}/scripts/trial_cuda_geocode_e2e.py" \
    --config "${cfg}" \
    --run-dir "${run_dir}" \
    --repeats "${repeats}" \
    --lines-per-block "${lines_per_block}" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "results: ${run_dir}/results.json"
