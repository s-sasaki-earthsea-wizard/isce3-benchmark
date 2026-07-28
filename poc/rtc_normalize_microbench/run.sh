#!/usr/bin/env bash
# Run the RTC normalize microbenchmark (container-side).
#
# Usage:
#   bash run.sh                 # full NISAR freq-A size (29240 x 21232)
#   bash run.sh 2000 2000       # smoke size
#   bash run.sh 29240 21232 3 5 # explicit rows cols reps_slow reps_fast
set -euo pipefail
cd "$(dirname "$0")"

[ -x ./bench_rtc_normalize ] || bash build.sh

export OMP_NUM_THREADS=${OMP_NUM_THREADS:-16}
echo "OMP_NUM_THREADS=$OMP_NUM_THREADS"
echo "host: $(hostname), date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "isce3 src: $(git -C "${ISCE3_SRC_MOUNT:-/opt/isce3-src}" rev-parse --short HEAD 2>/dev/null || echo 'n/a')"
echo
./bench_rtc_normalize "$@"
