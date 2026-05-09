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

: "${CONDA_PREFIX:?CONDA_PREFIX not set; entrypoint must activate the isce3 env first}"

if [ ! -f "${BUILD}/CMakeCache.txt" ]; then
    # Disable isce3's FetchContent fallbacks — all of these libraries are
    # already in the conda env and we want REQUIRED CONFIG to use them.
    # CMAKE_PREFIX_PATH points find_package() at the conda env's lib/cmake/...
    cmake -G Ninja \
        -S "${SRC}" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_INSTALL_PREFIX="${INSTALL}" \
        -DCMAKE_PREFIX_PATH="${CONDA_PREFIX}" \
        -DWITH_CUDA=On \
        -DISCE_CUDA_ARCHS="${ARCHS}" \
        -DCMAKE_CUDA_ARCHITECTURES="${ARCHS}" \
        -DISCE3_FETCH_EIGEN=OFF \
        -DISCE3_FETCH_GTEST=OFF \
        -DISCE3_FETCH_PYBIND11=OFF \
        -DISCE3_FETCH_PYRE=OFF
fi

cmake --build "${BUILD}" --parallel
cmake --install "${BUILD}"

echo "isce3 built at: ${INSTALL}"
echo "Tip: PYTHONPATH=${INSTALL}/packages   LD_LIBRARY_PATH=${INSTALL}/lib"
