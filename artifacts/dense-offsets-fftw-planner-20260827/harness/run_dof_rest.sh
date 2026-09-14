#!/bin/bash
# Phase C remaining arms, per PREREGISTRATION.md:
#   C-load : 3 replicates, OMP_NUM_THREADS=16, 15 spin workers
#   C-omp1 : 1 replicate,  OMP_NUM_THREADS=1,  no load  (falsification of A0-2)
set -uo pipefail
B=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
bash "$B/run_dof.sh" load 3 16 15
bash "$B/run_dof.sh" omp1 1 1 0
echo "=== phase C remaining arms complete ==="
