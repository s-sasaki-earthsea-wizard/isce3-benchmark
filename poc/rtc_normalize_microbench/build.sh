#!/usr/bin/env bash
# Build the RTC normalize microbenchmark (container-side).
#
# Matches the isce3 RelWithDebInfo build used for the #341 measurements:
# same conda GCC toolchain, -O2 -g -DNDEBUG. isce3 headers come from the
# read-only source mount; Eigen from the conda env (Eigen3_DIR points there
# in the isce3 CMake cache).
set -euo pipefail
cd "$(dirname "$0")"

CXX=${CXX:-/opt/micromamba/envs/isce3/bin/x86_64-conda-linux-gnu-c++}
ISCE3_SRC=${ISCE3_SRC_MOUNT:-/opt/isce3-src}
EIGEN_INC=${EIGEN_INC:-/opt/micromamba/envs/isce3/include/eigen3}

"$CXX" -O2 -g -DNDEBUG -fopenmp \
    -I "$ISCE3_SRC/cxx" -I "$EIGEN_INC" \
    -o bench_rtc_normalize bench_rtc_normalize.cpp

echo "built: $(pwd)/bench_rtc_normalize"
