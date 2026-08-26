#!/bin/bash
# CPU E2E A/B of the vectorized generate_insar_mask (isce3#354).
# Sequential control -> treat; run2 environment (isce3 v0.25.16 source +
# isce3-build-v0.25.16), CPU runconfig, NVMe out/scratch.
# Mirrors the recorded GPU E2E command from
# nisar-displacement/.claude-notes/2026-08-10-gpu-equivalence.md.
set -uo pipefail

BASE=/home/ew-s-sasaki-beacon/scratch/cpu_e2e_ab_20260816
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1

for mode in control treat; do
    echo "=== $mode start $(date -u +%FT%TZ) ==="
    ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
    ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T \
        -e AB_MODE=$mode \
        -v /mnt/nas/Projects/nisar-displacement/data:/data \
        -v /mnt/nas/Projects/nisar-displacement/configs:/configs \
        -v "$BASE":/ab \
        -v "$BASE/$mode/out":/out \
        -v "$BASE/$mode/scratch":/scratch \
        dev bash /ab/inner.sh > "$BASE/$mode/run.log" 2> "$BASE/$mode/run.err"
    rc=$?
    echo "=== $mode exit rc=$rc $(date -u +%FT%TZ) ==="
    tail -n 25 "$BASE/$mode/run.err" | grep -E "Elapsed|Maximum resident|Exit status" || true
    if [ $rc -ne 0 ]; then
        echo "ABORT: $mode failed rc=$rc — see $BASE/$mode/run.err"
        exit $rc
    fi
done
echo "=== both runs complete $(date -u +%FT%TZ) ==="
