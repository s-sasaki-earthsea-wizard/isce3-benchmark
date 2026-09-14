#!/bin/bash
# Runs INSIDE the dev container: one standalone dense_offsets replicate.
# No source overlay — stock v0.25.16 install.
# Variables under test are passed in from the host runner via env:
#   OMP_NUM_THREADS / MKL_NUM_THREADS  (OMP arm)
#   DOF_LOAD_WORKERS                   (synthetic CPU load during FFTW planning)
set -uo pipefail

echo "rep=${REP_ID:?} run_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset} MKL_NUM_THREADS=${MKL_NUM_THREADS:-unset}"
echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-unset} DOF_LOAD_WORKERS=${DOF_LOAD_WORKERS:-0}"
echo "nproc=$(nproc)"
python3 -c "import isce3, sys; print('isce3', isce3.__version__)"

# Optional synthetic CPU load: FFTW_MEASURE benchmarks candidate plans by
# wall-clock timing, so contention is the lever that can change plan choice
# without touching the source.
LOAD_PIDS=()
NW=${DOF_LOAD_WORKERS:-0}
if [ "$NW" -gt 0 ]; then
    for _ in $(seq 1 "$NW"); do
        ( while :; do :; done ) & LOAD_PIDS+=($!)
    done
    echo "started $NW spin workers: ${LOAD_PIDS[*]}"
fi
cleanup() { for p in "${LOAD_PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

/usr/bin/time -v python3 -m nisar.workflows.dense_offsets /ab/configs/dof_rep.yaml
rc=$?
echo "dense_offsets exit rc=$rc"
exit $rc
