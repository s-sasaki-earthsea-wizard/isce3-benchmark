#!/bin/bash
# Host-side: standalone dense_offsets replicates for one arm of the FFTW
# planner A/B (bench#48). Same protocol as bench#36 Step 2 Phase B idle:
# OMP=16, no synthetic load, host quiescence gate before each replicate.
#   usage: run_ab.sh <tag> <n_reps> [start_index]
# Env switches:
#   ARMB_WISDOM=1   import pinned wisdom (/ab/armB_wisdom.f) — arm B reps
#   WISDOM_GEN=1    generator run: export wisdom to /ab/armB_wisdom.f
#                   instead of the per-rep /out copy (outputs excluded
#                   from the comparison set)
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/fftw_ab_20260828
SEED=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16
TAG=${1:?usage: run_ab.sh <tag> <n> [start]}
N=${2:-3}
START=${3:-1}
cd "$BENCH" || exit 1

for i in $(seq "$START" $((START + N - 1))); do
    D="$BASE/dof_${TAG}_$i"
    rm -rf "$D"; mkdir -p "$D/out" "$D/scratch"
    bash "$BASE/wait_quiet.sh" 8 3 600
    git -C "$SRC" rev-parse HEAD > "$D/isce3_sha.txt"
    git -C "$SRC" status --porcelain >> "$D/isce3_sha.txt"

    FFTW_ENV=(-e PYCUAMPCOR_FFTW_LOG=1)
    if [ "${WISDOM_GEN:-0}" = "1" ]; then
        FFTW_ENV+=(-e PYCUAMPCOR_FFTW_WISDOM_EXPORT=/ab/armB_wisdom.f)
    else
        FFTW_ENV+=(-e PYCUAMPCOR_FFTW_WISDOM_EXPORT=/out/plan_wisdom.f)
    fi
    if [ "${ARMB_WISDOM:-0}" = "1" ]; then
        FFTW_ENV+=(-e PYCUAMPCOR_FFTW_WISDOM_IMPORT=/ab/armB_wisdom.f)
    fi

    echo "=== ab/$TAG rep$i start $(date -u +%FT%TZ) ==="
    ISCE3_SRC="$SRC" \
    ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T \
        -e REP_ID="${TAG}_$i" \
        -e OMP_NUM_THREADS=16 -e MKL_NUM_THREADS=16 \
        -e OPENBLAS_NUM_THREADS=16 \
        "${FFTW_ENV[@]}" \
        -v /mnt/nas/Projects/nisar-displacement/data:/data \
        -v "$BASE":/ab \
        -v "$SEED/phase0/scratch":/coreg:ro \
        -v "$D/out":/out \
        -v "$D/scratch":/scratch \
        dev bash /ab/ab_inner.sh > "$D/run.log" 2> "$D/run.err"
    rc=$?
    echo "=== ab/$TAG rep$i exit rc=$rc $(date -u +%FT%TZ) ==="
    grep -E "Elapsed|Maximum resident" "$D/run.err" | head -3
    grep "pycuampcor-fftw" "$D/run.err" | head -14
    if [ $rc -ne 0 ]; then
        echo "ABORT: ab/$TAG rep$i failed rc=$rc — see $D/run.err"
        exit $rc
    fi
    # Drop the 17 GB reference.slc copy (deterministic RSLC copy, proven in
    # Step 2); keep its hash as the input-side determinism check.
    sha256sum "$D"/scratch/dense_offsets/freqA/HH/reference.slc \
        > "$D/reference_slc.sha256" 2>/dev/null
    rm -f "$D"/scratch/dense_offsets/freqA/HH/reference.slc*
done
echo "=== ab/$TAG: reps $START..$((START + N - 1)) complete $(date -u +%FT%TZ) ==="
