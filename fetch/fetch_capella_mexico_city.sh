#!/usr/bin/env bash
# Fetch Capella Space open-data SICD scenes over Mexico City (Stage U0, bench#54).
#
# Source: s3://capella-open-data (public, anonymous read; also served over
#   https://capella-open-data.s3.amazonaws.com/ so no AWS credentials are needed).
#   The scenes below come from Capella's curated `capella-open-data-insar/`
#   use-case tree, site "Mexico City": 18 STRIPMAP SICD acquisitions,
#   satellite C14, polarization HH, 2-day cadence, 2024-06-26 .. 2024-08-15.
#
# Grid/Type is RGZERO (RMA / RG_DOP / INCA) for every SM_SICD in this stack,
# which is the half of SICD that maps directly onto an isce3 radar grid.
# See .claude-notes/2026-09-17-sicd-ecosystem-and-prior-art.md.
#
# Default set is the "pair": 2024-06-26 / 2024-06-29 (3-day repeat,
# B_perp -627.5 m, h_amb 12.4 m, 4.57 % of the critical baseline).
#
# Downloads are sequential, resumable (curl -C -) and size-verified against the
# server's Content-Length. Progress is one line per file so a log watcher can
# pick it up:
#   DONE  <file> <bytes> <seconds>
#   SKIP  <file> (already complete)
#   FAIL  <file> <reason>
# and finally "ALL DONE" or "SOME FAILED".
#
# Usage:
#   fetch/fetch_capella_mexico_city.sh [--set pair|all] [--out DIR] [--list]
set -uo pipefail

HTTP_BASE="https://capella-open-data.s3.amazonaws.com"

# Object keys, relative to HTTP_BASE. The stack is uniform, so the key is
# data/<Y>/<M>/<D>/<GRANULE>/<GRANULE>.ntf with no zero padding on M/D.
PAIR_GRANULES=(
  2024/6/26:CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055
  2024/6/29:CAPELLA_C14_SM_SICD_HH_20240629134910_20240629134915
)

OUT_DIR="data/capella_mexico_city"
SET="pair"
LIST_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --set)   SET="$2"; shift 2 ;;
    --out)   OUT_DIR="$2"; shift 2 ;;
    --list)  LIST_ONLY=1; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$SET" in
  pair) GRANULES=("${PAIR_GRANULES[@]}") ;;
  all)  echo "FAIL --set all not wired up yet (only the 3-day pair is listed)" >&2; exit 2 ;;
  *)    echo "unknown --set: $SET" >&2; exit 2 ;;
esac

mkdir -p "$OUT_DIR"

failed=0
for entry in "${GRANULES[@]}"; do
  datepath="${entry%%:*}"
  granule="${entry#*:}"
  key="data/${datepath}/${granule}/${granule}.ntf"
  url="${HTTP_BASE}/${key}"
  dest="${OUT_DIR}/${granule}.ntf"

  if [[ "$LIST_ONLY" == 1 ]]; then
    echo "$url"
    continue
  fi

  # Expected size from the server, so a truncated file is detected rather than
  # silently used.
  expected=$(curl -sI "$url" | awk 'tolower($1) == "content-length:" { gsub(/\r/, "", $2); print $2 }')
  if [[ -z "$expected" ]]; then
    echo "FAIL  ${granule}.ntf (no Content-Length from $url)"
    failed=1
    continue
  fi

  if [[ -f "$dest" ]]; then
    have=$(stat -c %s "$dest")
    if [[ "$have" == "$expected" ]]; then
      echo "SKIP  ${granule}.ntf (already complete)"
      continue
    fi
  fi

  start=$(date +%s)
  if ! curl -sS -C - -o "$dest" "$url"; then
    echo "FAIL  ${granule}.ntf (curl error)"
    failed=1
    continue
  fi
  elapsed=$(( $(date +%s) - start ))

  have=$(stat -c %s "$dest")
  if [[ "$have" != "$expected" ]]; then
    echo "FAIL  ${granule}.ntf (size $have != expected $expected)"
    failed=1
    continue
  fi
  echo "DONE  ${granule}.ntf $have $elapsed"
done

if [[ "$LIST_ONLY" == 1 ]]; then
  exit 0
fi

if [[ "$failed" == 0 ]]; then
  echo "ALL DONE"
else
  echo "SOME FAILED"
  exit 1
fi
