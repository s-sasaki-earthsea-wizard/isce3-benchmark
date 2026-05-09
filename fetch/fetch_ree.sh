#!/usr/bin/env bash
# Stage REE synthetic test fixtures from the isce3 source tree into ./data/REE/.
# Run on the HOST (not in the container) — uses the path from .env.
set -euo pipefail

if [ -f .env ]; then
    set -a; source .env; set +a
fi

: "${ISCE3_SRC:?ISCE3_SRC not set; copy .env.example to .env and edit it}"
: "${BENCH_DATA_DIR:=./data}"

src_dir="${ISCE3_SRC}/tests/data"
dst_dir="${BENCH_DATA_DIR}/REE"

if [ ! -d "${src_dir}" ]; then
    echo "ERROR: isce3 tests/data not found at ${src_dir}" >&2
    echo "Make sure ISCE3_SRC points at a checked-out isce3 tree." >&2
    exit 1
fi

mkdir -p "${dst_dir}"

# REE products + ancillaries used by the focus / gslc / gcov smoke configs.
patterns=(
    'REE_L0B_*.h5'
    'REE_RSLC_*.h5'
    'REE_GSLC_*.h5'
    'REE_*orbit*.xml'
    'REE_*attitude*.xml'
    'REE_DEM_*.tif'
    'REE_DEM_*.h5'
)

shopt -s nullglob
copied=0
for pat in "${patterns[@]}"; do
    for f in "${src_dir}"/${pat}; do
        cp -av "${f}" "${dst_dir}/"
        copied=$((copied + 1))
    done
done

if [ "${copied}" -eq 0 ]; then
    echo "WARN: no REE fixtures matched in ${src_dir}." >&2
    echo "      isce3 tests sometimes lazy-download these via Git LFS or CMake." >&2
    echo "      Check ${ISCE3_SRC}/tests/CMakeLists.txt and tests/data/README.md." >&2
    exit 1
fi

echo "staged ${copied} files into ${dst_dir}"
