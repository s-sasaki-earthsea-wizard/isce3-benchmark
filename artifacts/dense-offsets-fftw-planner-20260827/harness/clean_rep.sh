#!/bin/bash
# Remove a replicate directory that contains container-created (root-owned)
# files. A host-side `rm -rf` cannot unlink entries inside a root-owned
# directory, so the deletion is done from inside a container instead.
#   usage: clean_rep.sh <rep-dir-name> [<rep-dir-name> ...]
set -uo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
BENCH=/mnt/nas/Projects/third-party-projects/isce3/isce3-benchmark
cd "$BENCH" || exit 1
for name in "$@"; do
    case "$name" in
        rep[0-9]*|dof_*) ;;
        *) echo "refusing to clean '$name' (expected rep<N> or dof_<tag>_<n>)"; exit 2 ;;
    esac
    [ -e "$BASE/$name" ] || { echo "$name: absent, nothing to do"; continue; }
    echo "cleaning $name"
    ISCE3_SRC=/mnt/nas/Projects/third-party-projects/isce3-v0.25.16 \
    ISCE3_BUILD_DIR=./isce3-build-v0.25.16 \
    docker compose run --rm -T -v "$BASE":/ab dev \
        bash -c "rm -rf /ab/$name && echo removed /ab/$name" 2>&1 | tail -1
done
