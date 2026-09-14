#!/bin/bash
# Host-side: build the symlink-farm scratch for one replicate.
# Symlink targets use container paths (/phase0scratch/...) — they are
# dangling on the host and resolve inside the container.
set -euo pipefail
BASE=/home/ew-s-sasaki-beacon/scratch/bench36_step2_20260826
REP=$BASE/rep${1:?usage: make_rep_scratch.sh <n>}
P0=$BASE/phase0/scratch

mkdir -p "$REP/out" "$REP/scratch/rdr2geo/freqA"
ln -sfn /phase0scratch/geo2rdr "$REP/scratch/geo2rdr"
ln -sfn /phase0scratch/fine_resample_slc "$REP/scratch/fine_resample_slc"
for f in "$P0"/rdr2geo/freqA/*; do
    b=$(basename "$f")
    case "$b" in
        # InSAR_L1_writer regenerates BOTH <Type>_offsets_dem.rdr (line 466)
        # and <Type>_ifgram_dem.rdr (line 684) unconditionally, so these must
        # be writable real paths, not symlinks into the read-only phase0 mount.
        *_dem.rdr|*_dem.hdr) ;;
        *) ln -sfn "/phase0scratch/rdr2geo/freqA/$b" "$REP/scratch/rdr2geo/freqA/$b" ;;
    esac
done
echo "rep$1 scratch farm:"; ls -la "$REP/scratch" "$REP/scratch/rdr2geo/freqA" | head -30
