#!/usr/bin/env bash
# Build isce3 from the bind-mounted source tree at /opt/isce3-src into
# /opt/isce3-build. Idempotent: re-running just re-invokes ninja.
set -euo pipefail

SRC=${ISCE3_SRC_IN_CONTAINER:-/opt/isce3-src}
BUILD=${ISCE3_BUILD_IN_CONTAINER:-/opt/isce3-build}
INSTALL="${BUILD}/install"
ARCHS=${ISCE_CUDA_ARCHS:-Auto}

if [ ! -d "${SRC}" ]; then
    echo "ERROR: isce3 source not mounted at ${SRC}" >&2
    exit 1
fi

mkdir -p "${BUILD}"

if [ ! -f "${BUILD}/CMakeCache.txt" ]; then
    cmake -G Ninja \
        -S "${SRC}" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_INSTALL_PREFIX="${INSTALL}" \
        -DWITH_CUDA=On \
        -DISCE_CUDA_ARCHS="${ARCHS}" \
        -DCMAKE_CUDA_ARCHITECTURES="${ARCHS}"
fi

cmake --build "${BUILD}" --parallel
cmake --install "${BUILD}"

echo "isce3 built at: ${INSTALL}"
echo "Tip: PYTHONPATH=${INSTALL}/packages   LD_LIBRARY_PATH=${INSTALL}/lib"
