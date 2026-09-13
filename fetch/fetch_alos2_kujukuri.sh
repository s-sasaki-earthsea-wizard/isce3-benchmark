#!/usr/bin/env bash
# Fetch the JAXA PALSAR-2 sample L1.1 CEOS stack over Kujukuri (Chiba, Japan).
#
# Source: https://www.eorc.jaxa.jp/ALOS-2/en/doc/sam_index.htm
#   "[SAR time series] Chiba, Japan (Sep 2014 - Jan 2018) Stripmap 3m (HH)"
#   -> 12 acquisitions, frame 0700, ascending, L1.1 CEOS, ~6.0 GB zip each.
#   "[SAR interferometry] Chiba, Japan (Jan 15, 2015 and Mar 10, 2016)"
#   -> descending pair, frame 2900, L1.1 CEOS, ~5.7 GB zip each.
#
# Downloads are sequential (be gentle with JAXA's FTP), resumable (curl -C -),
# and size-verified against the server's Content-Length. Progress is written
# as one line per file so a log watcher can pick it up:
#   DONE  <file> <bytes> <seconds>
#   SKIP  <file> (already complete)
#   FAIL  <file> <reason>
# and finally "ALL DONE" or "SOME FAILED".
#
# Usage:
#   fetch/fetch_alos2_kujukuri.sh [--set asc|desc|all] [--first N] [--out DIR] [--list]
#
# Order for the ascending set: the three dates picked for the first
# workflow smoke test / one closure triangle come first (151020, 151103 = one
# 14-day repeat cycle; 160531 closes the triangle), then the rest by date.
set -uo pipefail

FTP_BASE="ftp://ftp.eorc.jaxa.jp/pub/ALOS-2/1501sample"

# Ascending time series (720_kuju_a). Use the uniform 2018-08 upload set
# (1000000000_*); the 1000000001..5_* files there are older 2017 reprocessings.
# Sizes observed on 2026-09-13: ~6.43-6.47 GB each.
ASC_DIR="720_kuju_a"
ASC_FILES=(
  1000000000_001001_ALOS2076070700-151020.zip
  1000000000_001001_ALOS2078140700-151103.zip
  1000000000_001001_ALOS2109190700-160531.zip
  1000000000_001001_ALOS2016040700-140909.zip
  1000000000_001001_ALOS2024320700-141104.zip
  1000000000_001001_ALOS2040880700-150224.zip
  1000000000_001001_ALOS2055370700-150602.zip
  1000000000_001001_ALOS2131960700-161101.zip
  1000000000_001001_ALOS2146450700-170207.zip
  1000000000_001001_ALOS2160940700-170516.zip
  1000000000_001001_ALOS2183710700-171017.zip
  1000000000_001001_ALOS2196130700-180109.zip
)

# Descending interferometric pair (710_kuju_d). Sizes ~5.68 GB each.
DESC_DIR="710_kuju_d"
DESC_FILES=(
  1000000001_001001_ALOS2034892900-150115.zip
  1000000002_001001_ALOS2096992900-160310.zip
)

SET="asc"
FIRST=0
OUT="$(cd "$(dirname "$0")/.." && pwd)/data/ALOS2-kujukuri"
LIST_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --set)   SET="$2"; shift 2 ;;
    --first) FIRST="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    --list)  LIST_ONLY=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Build the (subdir, file) job list.
JOBS=()
case "$SET" in
  asc)  for f in "${ASC_FILES[@]}";  do JOBS+=("$ASC_DIR/$f");  done ;;
  desc) for f in "${DESC_FILES[@]}"; do JOBS+=("$DESC_DIR/$f"); done ;;
  all)  for f in "${ASC_FILES[@]}";  do JOBS+=("$ASC_DIR/$f");  done
        for f in "${DESC_FILES[@]}"; do JOBS+=("$DESC_DIR/$f"); done ;;
  *) echo "bad --set: $SET (asc|desc|all)" >&2; exit 2 ;;
esac
if [ "$FIRST" -gt 0 ] 2>/dev/null; then JOBS=("${JOBS[@]:0:$FIRST}"); fi

if [ "$LIST_ONLY" -eq 1 ]; then
  printf '%s\n' "${JOBS[@]}"
  exit 0
fi

mkdir -p "$OUT"
echo "START $(date -u +%FT%TZ) set=$SET n=${#JOBS[@]} out=$OUT"

remote_size() {
  # Content-Length from an FTP SIZE request; empty on failure.
  curl -sI --max-time 60 "$1" | tr -d '\r' | awk 'tolower($1)=="content-length:"{print $2}'
}

nfail=0
for job in "${JOBS[@]}"; do
  subdir="${job%%/*}"; fname="${job##*/}"
  url="$FTP_BASE/$subdir/$fname"
  dest="$OUT/$fname"
  part="$dest.part"

  expected="$(remote_size "$url")"
  if [ -z "$expected" ]; then
    echo "FAIL  $fname could-not-read-remote-size"; nfail=$((nfail+1)); continue
  fi

  if [ -f "$dest" ] && [ "$(stat -c %s "$dest")" = "$expected" ]; then
    echo "SKIP  $fname (already complete, $expected bytes)"; continue
  fi

  t0=$(date +%s)
  # Resume if a partial exists; retry on transient errors; no progress meter
  # (this runs unattended and the log should stay one line per event).
  curl -sS -C - --ftp-pasv \
       --retry 8 --retry-delay 15 --retry-all-errors \
       --max-time 14400 --speed-time 120 --speed-limit 10000 \
       -o "$part" "$url"
  rc=$?
  t1=$(date +%s)

  if [ $rc -ne 0 ]; then
    echo "FAIL  $fname curl-exit=$rc after $((t1-t0))s (partial kept for resume)"
    nfail=$((nfail+1)); continue
  fi
  got="$(stat -c %s "$part" 2>/dev/null || echo 0)"
  if [ "$got" != "$expected" ]; then
    echo "FAIL  $fname size-mismatch got=$got expected=$expected (partial kept)"
    nfail=$((nfail+1)); continue
  fi
  mv -f "$part" "$dest"
  echo "DONE  $fname $got $((t1-t0))s"
done

echo "END $(date -u +%FT%TZ)"
if [ "$nfail" -eq 0 ]; then echo "ALL DONE"; else echo "SOME FAILED ($nfail)"; exit 1; fi
