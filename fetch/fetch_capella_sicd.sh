#!/usr/bin/env bash
# Fetch Capella open-data SICD scenes by granule id (bench#54 reinforcement set).
#
# Anonymous HTTPS against capella-open-data.s3.amazonaws.com, resumable
# (curl -C -), size-verified against Content-Length. One line per file:
#   DONE <file> <bytes> <seconds> | SKIP <file> | FAIL <file> <reason>
# then ALL DONE or SOME FAILED (exit 1).
#
# The object key is data/YYYY/M/D/<granule>/<granule>.ntf (month and day
# without leading zeros), derived from the granule's start time.
#
# Usage:
#   fetch/fetch_capella_sicd.sh --out DIR GRANULE [GRANULE ...]
#   fetch/fetch_capella_sicd.sh --out DIR --set reinforcement
set -uo pipefail

HTTP_BASE="https://capella-open-data.s3.amazonaws.com"
REINFORCEMENT=(
    # Niscemi, Italy -- right-looking ascending pair (C13, 3 d, B_perp 155.5 m)
    CAPELLA_C13_SM_SICD_HH_20260204114511_20260204114516
    CAPELLA_C13_SM_SICD_HH_20260207104155_20260207104201
    # Niscemi, Italy -- left-looking descending pair (C13, 3 d, B_perp 253.5 m, both 200 MHz)
    CAPELLA_C13_SM_SICD_HH_20260204183253_20260204183257
    CAPELLA_C13_SM_SICD_HH_20260207172937_20260207172942
    # Yumare, Venezuela -- right-looking descending, single scene (C15)
    CAPELLA_C15_SM_SICD_HH_20260627144157_20260627144201
)

OUT=""
GRANULES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT=${2:?}; shift ;;
        --set) [ "${2:-}" = reinforcement ] || { echo "unknown set: ${2:-}" >&2; exit 2; }
               GRANULES+=("${REINFORCEMENT[@]}"); shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) GRANULES+=("$1") ;;
    esac
    shift
done
[ -n "$OUT" ] && [ ${#GRANULES[@]} -gt 0 ] || { echo "usage: $0 --out DIR GRANULE... | --set reinforcement" >&2; exit 2; }
mkdir -p "$OUT"

nfail=0
for g in "${GRANULES[@]}"; do
    ts=${g#*_HH_}; ts=${ts%%_*}                      # YYYYMMDDhhmmss of the start
    y=${ts:0:4}; m=$((10#${ts:4:2})); d=$((10#${ts:6:2}))
    url="$HTTP_BASE/data/$y/$m/$d/$g/$g.ntf"
    dst="$OUT/$g.ntf"
    size=$(curl -sfI "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{print $2}')
    if [ -z "$size" ]; then echo "FAIL $g no Content-Length ($url)"; nfail=$((nfail+1)); continue; fi
    if [ -f "$dst" ] && [ "$(stat -c %s "$dst")" = "$size" ]; then echo "SKIP $g"; continue; fi
    t0=$(date +%s)
    if curl -sf -C - -o "$dst" "$url" && [ "$(stat -c %s "$dst")" = "$size" ]; then
        echo "DONE $g $size $(( $(date +%s) - t0 ))"
    else
        echo "FAIL $g size mismatch or curl error"; nfail=$((nfail+1))
    fi
done
[ $nfail -eq 0 ] && echo "ALL DONE" || { echo "SOME FAILED ($nfail)"; exit 1; }
