#!/usr/bin/env bash
# Pod-shaped (Runpod, no docker) runner for the full-subswath CUDA
# Geocode trial. See scripts/trial_cuda_geocode_subswath.py.
#
# Assumes:
#   - micromamba env "isce3" (docker/env-isce3-build.yml) at $MAMBA_ROOT_PREFIX
#   - from-source CUDA isce3 install at $ISCE3_INSTALL
#     (scripts/build_isce3.sh output; RPATH already patched)
#
# Usage:
#   scripts/run_trial_subswath_a100.sh <runconfig.yaml> [repeats] [lines_per_block] [extra args...]
set -euo pipefail

cfg="${1:?usage: run_trial_subswath_a100.sh <runconfig.yaml> [repeats] [lpb] [extra...]}"
repeats="${2:-3}"
lines_per_block="${3:-200}"
shift $(( $# > 3 ? 3 : $# ))

BENCH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-/opt/mamba}"
MICROMAMBA="${MICROMAMBA:-/workspace/mamba/bin/micromamba}"
ISCE3_INSTALL="${ISCE3_INSTALL:-/dev/shm/isce3-build/install}"
LOG_ROOT="${LOG_ROOT:-/workspace/logs_runpod-a100}"

export MAMBA_ROOT_PREFIX
export PYTHONPATH="${ISCE3_INSTALL}/packages:${BENCH_ROOT}/scripts${PYTHONPATH:+:${PYTHONPATH}}"
export LD_LIBRARY_PATH="${ISCE3_INSTALL}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

run_dir="${LOG_ROOT}/$(date -u +%Y%m%dT%H%M%SZ)_trial_subswath"
mkdir -p "${run_dir}"

{
    echo "# captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# host: $(hostname) (runpod pod, host-shared CPUs)"
    echo; echo "## kernel"; uname -a
    echo; echo "## cpu"; lscpu | grep -E "Model name|^CPU\(s\)|Thread|Socket|L3"
    echo; echo "## memory"; free -h | head -2
    echo; echo "## gpu"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
    echo; echo "## cuda"; nvcc --version | tail -2
    echo; echo "## isce3"
    echo "install: ${ISCE3_INSTALL}"
    (cd /opt/src/isce3 2>/dev/null && git log --oneline -1) || true
    echo; echo "## bench repo"
    (cd "${BENCH_ROOT}" && git log --oneline -1 && git status --short | head)
    echo; echo "## env"
    echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset}"
} > "${run_dir}/provenance.txt"

"${MICROMAMBA}" run -n isce3 python "${BENCH_ROOT}/scripts/trial_cuda_geocode_subswath.py" \
    --config "${cfg}" \
    --run-dir "${run_dir}" \
    --repeats "${repeats}" \
    --lines-per-block "${lines_per_block}" \
    "$@" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "results: ${run_dir}/results.json"
