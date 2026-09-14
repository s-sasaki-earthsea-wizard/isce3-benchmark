#!/bin/bash
# Host-side: standalone dense_offsets replicates.
#   usage: run_dof.sh <tag> <n_reps> [omp_threads] [load_workers]
# Each replicate gets a fresh /scratch; the coarse-resampled secondary is
# mounted read-only from the phase0 scratch so the input is byte-identical
# across replicates by construction.
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
TAG=${1:?usage: run_dof.sh <tag> <n> [omp] [load]}
N=${2:-3}
OMP=${3:-16}
LOAD=${4:-0}
cd "$BENCH" || exit 1

for i in $(seq 1 "$N"); do
    D="$BASE/dof_${TAG}_$i"
    rm -rf "$D"; mkdir -p "$D/out" "$D/scratch"
    echo "=== dof/$TAG rep$i start $(date -u +%FT%TZ) omp=$OMP load=$LOAD ==="
    ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
    ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T \
        -e REP_ID="${TAG}_$i" \
        -e OMP_NUM_THREADS="$OMP" -e MKL_NUM_THREADS="$OMP" \
        -e OPENBLAS_NUM_THREADS="$OMP" -e DOF_LOAD_WORKERS="$LOAD" \
        -v /mnt/nas/Projects/nisar-displacement/data:/data \
        -v "$BASE":/ab \
        -v "$BASE/phase0/scratch":/coreg:ro \
        -v "$D/out":/out \
        -v "$D/scratch":/scratch \
        dev bash /ab/dof_inner.sh > "$D/run.log" 2> "$D/run.err"
    rc=$?
    echo "=== dof/$TAG rep$i exit rc=$rc $(date -u +%FT%TZ) ==="
    grep -E "Elapsed|Maximum resident" "$D/run.err" | head -3
    if [ $rc -ne 0 ]; then
        echo "ABORT: dof/$TAG rep$i failed rc=$rc — see $D/run.err"
        exit $rc
    fi
    # Drop the 17 GB reference.slc copy; it is a deterministic RSLC copy and
    # the Ampcor outputs are what we compare.
    sha256sum "$D"/scratch/dense_offsets/freqA/HH/reference.slc \
        > "$D/reference_slc.sha256" 2>/dev/null
    rm -f "$D"/scratch/dense_offsets/freqA/HH/reference.slc*
done
echo "=== dof/$TAG: $N reps complete $(date -u +%FT%TZ) ==="
