#!/bin/bash
# usage: run_fftw_probe.sh <tag> <n_runs> [load_workers]
# Builds once, then runs the probe N times, optionally under synthetic CPU load.
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
TAG=${1:?usage: run_fftw_probe.sh <tag> <n> [load]}
N=${2:-5}
LOAD=${3:-0}
cd "$BENCH" || exit 1

ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
docker compose run --rm -T -v "$BASE":/ab dev bash -c "
set -e
cd /ab/fftw_probe
PREFIX=\${CONDA_PREFIX:-/opt/micromamba/envs/isce3}
gcc -O2 -I\$PREFIX/include -o fftw_plan_probe fftw_plan_probe.c -L\$PREFIX/lib -Wl,-rpath,\$PREFIX/lib -lfftw3f -lm
echo 'built OK'
LOAD_PIDS=()
if [ ${LOAD} -gt 0 ]; then
  for _ in \$(seq 1 ${LOAD}); do ( while :; do :; done ) & LOAD_PIDS+=(\$!); done
  echo \"spin workers: ${LOAD}\"
fi
mkdir -p /ab/fftw_probe/dump_${TAG}
rm -f /ab/fftw_probe/dump_${TAG}/*.bin
for i in \$(seq 1 ${N}); do
  echo \"--- ${TAG} run\$i ---\"
  ./fftw_plan_probe \$i /ab/fftw_probe/dump_${TAG}
done
for p in \"\${LOAD_PIDS[@]:-}\"; do kill \$p 2>/dev/null || true; done
" > "$BASE/fftw_probe/${TAG}.txt" 2>&1
rc=$?
echo "=== fftw probe '$TAG' rc=$rc ==="
grep -c "plan_hash" "$BASE/fftw_probe/${TAG}.txt" 2>/dev/null
for t in raw_r2c raw_c2r oversampled_r2c oversampled_c2r; do
  np=$(grep "^$t " "$BASE/fftw_probe/${TAG}.txt" | grep -o 'plan_hash=[0-9a-f]*' | sort -u | wc -l)
  no=$(grep "^$t " "$BASE/fftw_probe/${TAG}.txt" | grep -o 'out_hash=[0-9a-f]*'  | sort -u | wc -l)
  ni=$(grep "^$t " "$BASE/fftw_probe/${TAG}.txt" | grep -o 'in_hash=[0-9a-f]*'   | sort -u | wc -l)
  printf "  %-18s distinct plans=%s  distinct outputs=%s  distinct inputs=%s\n" "$t" "$np" "$no" "$ni"
done
