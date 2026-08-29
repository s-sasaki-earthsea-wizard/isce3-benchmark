#!/bin/bash
# Host-side: check out one A/B arm ref in the dedicated v0.25.16 source tree
# and rebuild incrementally (2 TUs + relink) via scripts/build_isce3.sh,
# which also runs the mandatory patchelf RPATH rewrite after install.
#   usage: build_arm.sh <git-ref> <arm-tag>
set -euo pipefail
SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
BASE=/home/ew-s-sasaki-beacon/scratch/fftw_ab_20260828
REF=${1:?usage: build_arm.sh <git-ref> <arm-tag>}
TAG=${2:?usage: build_arm.sh <git-ref> <arm-tag>}

cd "$SRC"
if [ -n "$(git status --porcelain)" ]; then
    echo "ABORT: $SRC working tree not clean" >&2
    exit 1
fi
git switch --detach --quiet "$REF"
SHA=$(git rev-parse HEAD)

mkdir -p "$BASE/builds"
PROV="$BASE/builds/${TAG}.provenance.txt"
{
    echo "arm=$TAG ref=$REF sha=$SHA built_utc=$(date -u +%FT%TZ)"
    echo "diff vs v0.25.16:"
    git diff --stat v0.25.16 HEAD
} > "$PROV"

cd "$BENCH"
ISCE3_SRC="$SRC" ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T dev bash scripts/build_isce3.sh \
    > "$BASE/builds/${TAG}.build.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    echo "ABORT: build failed rc=$rc — see $BASE/builds/${TAG}.build.log" >&2
    exit $rc
fi
sha256sum "$BENCH"/isce3-build-v0.25.16/install/lib/libisce3.so* >> "$PROV"
echo "arm $TAG built: $SHA"
tail -3 "$BASE/builds/${TAG}.build.log"
