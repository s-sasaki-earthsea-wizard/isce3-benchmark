#!/bin/bash
# Host-side wrapper for Phase 0. Mirrors artifacts/cpu-e2e-nondeterminism-20260816/run_ab.sh
# (run2 environment: isce3 v0.25.16 source + isce3-build-v0.25.16).
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1

{
  echo "host=$(hostname)  date=$(date -u +%FT%TZ)"
  echo "isce3_src=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 @ $(git -C /mnt/nas/Projects/third-party-projects/isce3-v0.25.16 rev-parse HEAD)"
  echo "bench_repo @ $(git -C "$BENCH" rev-parse HEAD)"
  echo "config_sha256=$(sha256sum "$BASE/configs/insar_phase0.yaml" | cut -d' ' -f1)"
} > "$BASE/phase0/provenance.txt"

echo "=== phase0 start $(date -u +%FT%TZ) ==="
ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
docker compose run --rm -T \
    -v /mnt/nas/Projects/nisar-displacement/data:/data \
    -v "$BASE":/ab \
    -v "$BASE/phase0/out":/out \
    -v "$BASE/phase0/scratch":/scratch \
    dev bash /ab/phase0_inner.sh > "$BASE/phase0/run.log" 2> "$BASE/phase0/run.err"
rc=$?
echo "=== phase0 exit rc=$rc $(date -u +%FT%TZ) ==="
tail -n 25 "$BASE/phase0/run.err" | grep -E "Elapsed|Maximum resident|Exit status" || true
exit $rc
