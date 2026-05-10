#!/usr/bin/env bash
# Stage REE synthetic test fixtures from the isce3 source tree into ./data/REE/.
# Run on the HOST (not in the container) — uses the path from .env.
#
# isce3 keeps a self-contained focus test set at tests/data/focus/ in which all
# inputs are reachable as bare filenames (the directory uses symlinks to gather
# inputs from sibling subdirectories like tests/data/bf/). We just dereference
# the whole thing into data/REE/ so the same template runconfig.yaml works
# inside the container at /data/REE/ without any path rewriting.
set -euo pipefail

if [ -f .env ]; then
    set -a; source .env; set +a
fi

: "${ISCE3_SRC:?ISCE3_SRC not set; copy .env.example to .env and edit it}"
: "${BENCH_DATA_DIR:=./data}"

src_dir="${ISCE3_SRC}/tests/data/focus"
dst_dir="${BENCH_DATA_DIR}/REE"

if [ ! -d "${src_dir}" ]; then
    echo "ERROR: focus fixture dir not found at ${src_dir}" >&2
    echo "Make sure ISCE3_SRC points at a checked-out isce3 tree." >&2
    exit 1
fi

mkdir -p "${dst_dir}"

# -L dereferences symlinks so the destination is self-contained.
# -a preserves attrs but `--no-preserve=ownership` because root may not be us.
cp -aL --no-preserve=ownership "${src_dir}"/. "${dst_dir}/"

count="$(find "${dst_dir}" -maxdepth 1 -type f | wc -l)"
echo "staged ${count} files into ${dst_dir}"
ls -la "${dst_dir}"
