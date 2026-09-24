#!/usr/bin/env bash
#
# Per-scene / per-variant loops of the Capella RTC comparison (bench#54, PR #57),
# run inside the dev container by the Makefile targets capella-convert-beta0,
# capella-multirtc and capella-rtc-compare.
#
# Every iteration's exit status is propagated: the first failing scene or
# variant stops the loop and the script exits non-zero, so a stale output from
# an earlier run can never be mistaken for a fresh one (tests/test_capella_rtc_steps.py).
#
# Usage: scripts/capella_rtc_steps.sh convert-beta0 | multirtc | compare
#
# Environment (defaults are the container paths):
#   CAPELLA_DATA   /data/capella_mexico_city
#   PYTHON         python
#   MULTIRTC_SITE  /data/external/multirtc-site   (prepended to PYTHONPATH for multirtc)
set -uo pipefail

CAPELLA_DATA=${CAPELLA_DATA:-/data/capella_mexico_city}
PYTHON=${PYTHON:-python}
MULTIRTC_SITE=${MULTIRTC_SITE:-/data/external/multirtc-site}
SCENES=(20240626150051_20240626150055 20240629134910_20240629134915)
VARIANTS=(stock matched matched-tfix matched-tfix-rfix)
REF_SCENE=CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055

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

multirtc() {
    export PYTHONPATH="$MULTIRTC_SITE${PYTHONPATH:+:$PYTHONPATH}"
    for v in "${VARIANTS[@]}"; do
        local w="$CAPELLA_DATA/multirtc/20240626/$v"
        mkdir -p "$w" || die "mkdir $w"
        "$PYTHON" tools/multirtc_capella_rtc.py rtc "$CAPELLA_DATA/$REF_SCENE.ntf" --variant "$v" \
            --dem "$CAPELLA_DATA/dem.tif" --resolution 5 --work-dir "$w" \
            > "$w/console.log" 2>&1 || die "multirtc $v (see $w/console.log)"
        echo "MULTIRTC-OK $v"
    done
}

compare() {
    mkdir -p "$CAPELLA_DATA/rtc_compare" || die "mkdir"
    for v in "${VARIANTS[@]}"; do
        "$PYTHON" tools/compare_rtc.py --gcov "$CAPELLA_DATA/gcov/20240626/gcov_20240626.h5" \
            --rslc "$CAPELLA_DATA/rslc_beta0/20240626.h5" \
            --multirtc-run "$CAPELLA_DATA/multirtc/20240626/$v" \
            --out "$CAPELLA_DATA/rtc_compare/20240626_$v.json" > /dev/null \
            || die "compare $v"
        echo "COMPARE-OK $v"
    done
}

case "${1:-}" in
    convert-beta0) convert_beta0 ;;
    multirtc)      multirtc ;;
    compare)       compare ;;
    *) echo "usage: $0 convert-beta0 | multirtc | compare" >&2; exit 2 ;;
esac
