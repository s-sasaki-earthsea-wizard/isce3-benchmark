#!/bin/bash
# Run the Phase A comparison INSIDE the dev container.
# snaphu-py creates its scratch files with mkstemp (mode 0600, owned by the
# container's root), so a host-side comparison cannot read them. Running the
# comparison in-container reads them without changing any permissions.
#   usage: run_compare_reps.sh [rep numbers...]
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1
ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
docker compose run --rm -T -e STEP2_BASE=/ab -v "$BASE":/ab dev \
    python3 /ab/compare_reps.py "$@" 2>&1 | grep -v "^ Container"
