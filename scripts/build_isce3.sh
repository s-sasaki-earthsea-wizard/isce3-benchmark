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
    #
    # CMAKE_INSTALL_RPATH: conda-forge installs `isce3` (CPU-only 0.25.x) as a
    # transitive dep of `compass`, which leaves a libisce3.so.0 in
    # ${CONDA_PREFIX}/lib. Without this, isce3's own CMakeLists hard-bakes
    # ${CONDA_PREFIX}/lib as RPATH and the dynamic linker resolves
    # libisce3.so.0 to the conda copy (older, missing symbols our pybind
    # ext.so expects). Putting our install/lib first overrides that.
    cmake -G Ninja \
        -S "${SRC}" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_INSTALL_PREFIX="${INSTALL}" \
        -DCMAKE_PREFIX_PATH="${CONDA_PREFIX}" \
        -DCMAKE_INSTALL_RPATH="${INSTALL}/lib;${CONDA_PREFIX}/lib" \
        -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
        -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF \
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

# Force install/lib to win over CONDA_PREFIX/lib in the RPATH search.
# isce3's CMakeLists prepends ${CONDA_PREFIX}/lib regardless of what we set in
# CMAKE_INSTALL_RPATH, so the dynamic linker resolves libisce3.so.0 to the
# (older, ABI-incompatible) conda-forge `isce3` package that gets pulled in
# as a transitive dep of `compass`. Rewrite the RPATH unconditionally here.
if command -v patchelf >/dev/null 2>&1; then
    while IFS= read -r f; do
        if readelf -d "$f" 2>/dev/null | grep -qE '(RPATH|RUNPATH)'; then
            patchelf --set-rpath "${INSTALL}/lib:${CONDA_PREFIX}/lib" "$f"
        fi
    done < <(find "${INSTALL}/lib" "${INSTALL}/packages" -type f \
                  \( -name '*.so' -o -name '*.so.*' \))
    echo "RPATH rewritten to ${INSTALL}/lib:${CONDA_PREFIX}/lib"
else
    echo "WARN: patchelf not available; isce3 may import conda's libisce3 instead of ours" >&2
fi

echo "isce3 built at: ${INSTALL}"
echo "Tip: PYTHONPATH=${INSTALL}/packages   LD_LIBRARY_PATH=${INSTALL}/lib"
