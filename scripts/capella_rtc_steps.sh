#!/usr/bin/env bash
#
# Per-scene loops of the Capella RTC work (bench#54), run inside the dev
# container by the Makefile targets (capella-convert-beta0; the GCOV / MultiRTC
# comparison adds its steps here).
#
# Every iteration's exit status is propagated: the first failing scene or
# variant stops the loop and the script exits non-zero, so a stale output from
# an earlier run can never be mistaken for a fresh one (tests/test_capella_rtc_steps.py).
#
# Usage: scripts/capella_rtc_steps.sh convert-beta0
#
# Environment (defaults are the container paths):
#   CAPELLA_DATA   /data/capella_mexico_city
#   PYTHON         python
set -uo pipefail

CAPELLA_DATA=${CAPELLA_DATA:-/data/capella_mexico_city}
PYTHON=${PYTHON:-python}
SCENES=(20240626150051_20240626150055 20240629134910_20240629134915)

die() { echo "FAIL $*" >&2; exit 1; }

convert_beta0() {
    mkdir -p "$CAPELLA_DATA/rslc_beta0" "$CAPELLA_DATA/logs" || die "mkdir"
    for s in "${SCENES[@]}"; do
        local d=${s:0:8}
        "$PYTHON" tools/sicd_to_nisar_rslc.py "$CAPELLA_DATA/CAPELLA_C14_SM_SICD_HH_$s.ntf" \
            "$CAPELLA_DATA/rslc_beta0/$d.h5" --radiometry beta0 --overwrite \
            > "$CAPELLA_DATA/logs/convert_beta0_$d.log" 2>&1 \
            || die "convert-beta0 $d (see $CAPELLA_DATA/logs/convert_beta0_$d.log)"
        echo "CONVERT-OK $d"
    done
}

case "${1:-}" in
    convert-beta0) convert_beta0 ;;
    *) echo "usage: $0 convert-beta0" >&2; exit 2 ;;
esac
