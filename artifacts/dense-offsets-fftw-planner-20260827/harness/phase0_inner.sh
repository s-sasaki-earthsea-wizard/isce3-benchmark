#!/bin/bash
# Phase 0 (bench#36 Step 2): regenerate full CPU E2E scratch state.
# Runs INSIDE the isce3-benchmark dev container.
# Deviations from the recorded E2E control run:
#   1. intermediate_files_removal_enabled: false (file retention only)
#   2. unwrap.py overlay: delete_scratch=True -> False in the snaphu.unwrap
#      call, so the exact SNAPHU solver inputs (igram/coh/conf/cost) are
#      retained under scratch/unwrap/ for hash-pinning + pure-solver replay.
# Neither deviation touches numerics.
set -euo pipefail

cp -r /opt/isce3-build/install/packages /tmp/ov
sed -i 's/delete_scratch=True/delete_scratch=False/' /tmp/ov/nisar/workflows/unwrap.py
echo "overlay delete_scratch lines:"
grep -n "delete_scratch" /tmp/ov/nisar/workflows/unwrap.py
export PYTHONPATH=/tmp/ov:${PYTHONPATH:-}

UW=$(python3 -c 'import nisar.workflows.unwrap as u; print(u.__file__)')
echo "unwrap module: $UW"
if [ "$UW" != "/tmp/ov/nisar/workflows/unwrap.py" ]; then
    echo "FATAL: overlay not resolved (got $UW)" >&2
    exit 9
fi
echo "phase0 run_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset} MKL_NUM_THREADS=${MKL_NUM_THREADS:-unset}"

exec /usr/bin/time -v python3 -m nisar.workflows.insar \
    /ab/configs/insar_phase0.yaml --restart
