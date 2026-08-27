#!/bin/bash
# Run the Phase C comparison INSIDE the dev container (Ampcor outputs are
# written by the container's root; reference.slc is mode 0600).
#   usage: run_compare_dof.sh <tag> [<tag> ...]
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1
ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
docker compose run --rm -T -e STEP2_BASE=/ab -v "$BASE":/ab dev \
    python3 /ab/compare_dof.py "$@" 2>&1 | grep -v "^ Container"
