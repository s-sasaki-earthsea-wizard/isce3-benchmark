#!/usr/bin/env bash
# Build + run the geocode_slc PoC microbenchmark and archive the output.
# Intended to run inside the dev container (make poc-geocode-slc), where
# /logs is the host BENCH_LOG_DIR mount; falls back to ./logs_poc outside.
set -euo pipefail
cd "$(dirname "$0")"

LOG_ROOT="${POC_OUT_DIR:-/logs}"
[ -d "$LOG_ROOT" ] || LOG_ROOT="../../logs_poc"
OUT_DIR="${LOG_ROOT}/poc_geocode_slc/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT_DIR"

bash build.sh

{
    echo "# provenance"
    date -u +%Y-%m-%dT%H:%M:%SZ
    nvcc --version | tail -1
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
    echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset}"
    git -C ../.. rev-parse HEAD 2>/dev/null || echo "git SHA unavailable"
    echo
} | tee "$OUT_DIR/provenance.txt"

# Baseline geogrid + a 2x problem-size scaling point.
./geocode_slc_poc --csv "$OUT_DIR/results.csv" | tee "$OUT_DIR/scale1.txt"
./geocode_slc_poc --scale 2 --csv "$OUT_DIR/results.csv" | tee "$OUT_DIR/scale2.txt"

echo
echo "results archived under: $OUT_DIR"
