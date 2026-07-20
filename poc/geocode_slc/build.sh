#!/usr/bin/env bash
# Build the geocode_slc PoC microbenchmark (issue #11).
# POC_ARCH overrides the GPU arch (default: native, i.e. the visible GPU).
#   e.g. POC_ARCH=sm_80 for A100 cross-compile, POC_ARCH=sm_120 for RTX 5080.
set -euo pipefail
cd "$(dirname "$0")"

ARCH="${POC_ARCH:-native}"

nvcc -O3 -std=c++17 -arch="${ARCH}" \
    -Xcompiler -fopenmp,-O3,-Wall \
    geocode_slc_poc.cu -o geocode_slc_poc

echo "built poc/geocode_slc/geocode_slc_poc (arch=${ARCH})"
