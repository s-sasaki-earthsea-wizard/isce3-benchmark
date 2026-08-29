#!/bin/bash
# Run the A/B comparison INSIDE the dev container (Ampcor outputs are
# written by the container's root).
#   usage: run_compare_ab.sh <tag> [<tag> ...]     e.g. ctrl armA armB
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/fftw_ab_20260828
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1
ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
docker compose run --rm -T -e STEP2_BASE=/ab -v "$BASE":/ab dev \
    python3 /ab/compare_dof.py "$@" 2>&1 | grep -v "^ Container"
