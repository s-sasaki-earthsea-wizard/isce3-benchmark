#!/bin/bash
# Seed the A/B scratch base from this bundle. Idempotent.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BASE=/home/ew-s-sasaki-beacon/scratch/fftw_ab_20260828
SEED=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826

[ -d "$SEED/phase0/scratch/coarse_resample_slc" ] || {
    echo "ABORT: bench36 phase0 scratch missing at $SEED" >&2; exit 1; }

mkdir -p "$BASE/configs" "$BASE/builds"
cp "$HERE/ab_inner.sh" "$HERE/wait_quiet.sh" "$HERE/compare_dof.py" "$BASE/"
cp "$HERE/../configs/dof_rep.yaml" "$BASE/configs/"
sha256sum "$BASE/configs/dof_rep.yaml"
echo "A/B base ready at $BASE"
