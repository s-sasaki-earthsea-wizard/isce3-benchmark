#!/bin/bash
# Runs INSIDE the dev container: one unwrap-step replicate.
# Same overlay discipline as phase0 (delete_scratch=False only).
set -euo pipefail

cp -r /opt/isce3-build/install/packages /tmp/ov
sed -i 's/delete_scratch=True/delete_scratch=False/' /tmp/ov/nisar/workflows/unwrap.py
grep -n "delete_scratch" /tmp/ov/nisar/workflows/unwrap.py
export PYTHONPATH=/tmp/ov:${PYTHONPATH:-}

UW=$(python3 -c 'import nisar.workflows.unwrap as u; print(u.__file__)')
echo "unwrap module: $UW"
[ "$UW" = "/tmp/ov/nisar/workflows/unwrap.py" ] || { echo "FATAL: overlay not resolved" >&2; exit 9; }

echo "rep=${REP_ID:?} run_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset} MKL_NUM_THREADS=${MKL_NUM_THREADS:-unset}"
sha256sum /seed/RIFG.h5

exec /usr/bin/time -v python3 /ab/replay_unwrap.py /ab/configs/unwrap_rep.yaml
