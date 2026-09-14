#!/bin/bash
# Host-side: run N sequential unwrap-step replicates on the phase0-seeded
# scratch. Sequential on purpose — mirrors E2E single-run load conditions.
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
N=${1:-3}
cd "$BENCH" || exit 1

for i in $(seq 1 "$N"); do
    bash "$BASE/make_rep_scratch.sh" "$i" || exit 1
    echo "=== rep$i start $(date -u +%FT%TZ) ==="
    ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
    ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T \
        -e REP_ID="$i" \
        -v /mnt/nas/Projects/nisar-displacement/data:/data \
        -v "$BASE":/ab \
        -v "$BASE/phase0/scratch":/phase0scratch:ro \
        -v "$BASE/phase0/scratch/RIFG.h5":/seed/RIFG.h5:ro \
        -v "$BASE/rep$i/out":/out \
        -v "$BASE/rep$i/scratch":/scratch \
        dev bash /ab/rep_inner.sh > "$BASE/rep$i/run.log" 2> "$BASE/rep$i/run.err"
    rc=$?
    echo "=== rep$i exit rc=$rc $(date -u +%FT%TZ) ==="
    tail -n 25 "$BASE/rep$i/run.err" | grep -E "Elapsed|Maximum resident|Exit status" || true
    if [ $rc -ne 0 ]; then
        echo "ABORT: rep$i failed rc=$rc — see $BASE/rep$i/run.err"
        exit $rc
    fi
done
echo "=== all $N reps complete $(date -u +%FT%TZ) ==="
