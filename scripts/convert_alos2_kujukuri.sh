#!/usr/bin/env bash
# Convert every JAXA ALOS-2 L1.1 CEOS zip under data/ALOS2-kujukuri/ into a
# NISAR-format RSLC HDF5 with isce3's bundled example converter
# (share/nisar/examples/alos2_to_nisar_l1.py), inside the dev container.
#
# Provenance choice: the converter runs against the isce3 v0.25.16 build and
# source tree (ISCE3_BUILD_DIR / ISCE3_SRC below) so the RSLCs come from the
# same isce3 release as the validated GUNW chain in nisar-displacement
# (scripts/run_gunw_batch.sh there). The converter script itself is
# byte-identical between the v0.25.16 tree and current develop.
#
# Per scene: unzip (skipped if already extracted) -> convert -> on success
# delete the extracted 6.4 GB IMG-* file (the zip is kept as the source of
# truth; LED/VOL/TRL/summary/BRS stay for provenance). One log line per
# event so the run can be watched from a log tail:
#   UNZIP-OK <date> <s> | CONVERT-OK <date> <s> <bytes> | CONVERT-FAIL <date> ...
#   SKIP <date> (rslc exists) | ALL DONE | SOME FAILED
#
# Usage: scripts/convert_alos2_kujukuri.sh [--only YYMMDD ...]
set -uo pipefail
cd "$(dirname "$0")/.."
BENCH=$(pwd)
DATA=$BENCH/data/ALOS2-kujukuri
ISCE3_SRC=${ISCE3_SRC:-/mnt/nas/Projects/third-party-projects/isce3-v0.25.16}
ISCE3_BUILD_DIR=${ISCE3_BUILD_DIR:-./isce3-build-v0.25.16}
export ISCE3_SRC ISCE3_BUILD_DIR

ONLY=()
if [ "${1:-}" = "--only" ]; then shift; ONLY=("$@"); fi

mkdir -p "$DATA/ceos" "$DATA/rslc" "$DATA/logs"
echo "START $(date -u +%FT%TZ) isce3_src=$ISCE3_SRC build=$ISCE3_BUILD_DIR"
nfail=0
for z in "$DATA"/1000000000_001001_ALOS2*.zip; do
  fname=$(basename "$z")
  d6=${fname##*-}; d6=${d6%.zip}            # YYMMDD
  date8="20$d6"
  if [ ${#ONLY[@]} -gt 0 ] && ! printf '%s\n' "${ONLY[@]}" | grep -qx "$d6"; then continue; fi
  out="$DATA/rslc/$date8.h5"
  if [ -s "$out" ]; then echo "SKIP $date8 (rslc exists)"; continue; fi

  cdir="$DATA/ceos/$d6"
  if ! ls "$cdir"/IMG-HH-ALOS2* >/dev/null 2>&1; then
    t0=$(date +%s)
    if unzip -q -o "$z" -d "$cdir"; then
      echo "UNZIP-OK $date8 $(( $(date +%s)-t0 ))s"
    else
      echo "UNZIP-FAIL $date8"; nfail=$((nfail+1)); continue
    fi
  fi

  t0=$(date +%s)
  rm -f "$out"
  docker compose run --rm -T dev bash -c "
    export PYTHONPATH=/opt/isce3-src/share/nisar/examples:\$PYTHONPATH
    cd /data/ALOS2-kujukuri
    /usr/bin/time -v python /opt/isce3-src/share/nisar/examples/alos2_to_nisar_l1.py \
        -i ceos/$d6 -o rslc/$date8.h5 -v
  " > "$DATA/logs/convert_$date8.log" 2>&1
  rc=$?
  t1=$(date +%s)
  if [ $rc -eq 0 ] && [ -s "$out" ] && grep -q "saved file:" "$DATA/logs/convert_$date8.log"; then
    echo "CONVERT-OK $date8 $((t1-t0))s $(stat -c %s "$out")"
    rm -f "$cdir"/IMG-HH-ALOS2*
  else
    echo "CONVERT-FAIL $date8 rc=$rc after $((t1-t0))s (see logs/convert_$date8.log)"
    nfail=$((nfail+1))
  fi
done
echo "END $(date -u +%FT%TZ)"
if [ $nfail -eq 0 ]; then echo "ALL DONE"; else echo "SOME FAILED ($nfail)"; exit 1; fi
