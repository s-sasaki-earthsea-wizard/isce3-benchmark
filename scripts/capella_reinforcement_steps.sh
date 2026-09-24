#!/usr/bin/env bash
#
# Per-scene steps for the Capella reinforcement set (bench#54), run inside the
# dev container by the Makefile targets capella-rein-*. Same contract as
# scripts/capella_rtc_steps.sh: the first failing scene stops the step and the
# script exits non-zero, so a stale output is never mistaken for a fresh one.
#
# Usage: scripts/capella_reinforcement_steps.sh dem | convert | convert-beta0 | geometry | gcov
#                                               | multirtc | rtc-compare
#
# multirtc / rtc-compare repeat the Mexico City MultiRTC cross-check on one
# right-looking scene (RTC_SCENE), where MultiRTC's start time is right and only
# its starting range differs; they need make multirtc-setup and the gcov step.
#
# Scenes (short id = start time YYYYMMDDhhmmss):
#   20260204114511  Niscemi   right-looking ascending   (pair niscemi_ra, ref)
#   20260207104155  Niscemi   right-looking ascending   (pair niscemi_ra, sec)
#   20260204183253  Niscemi   left-looking descending   (pair niscemi_ld, ref)
#   20260207172937  Niscemi   left-looking descending   (pair niscemi_ld, sec)
#   20260627144157  Yumare    right-looking descending  (single scene)
#
# Environment (defaults are the container paths):
#   REIN     /data/capella_reinforcement
#   SCRATCH  /data/capella_reinforcement/scratch
#   PYTHON   python
#   TIME     /usr/bin/time -v   (wraps the GCOV run; empty to disable)
#   RTC_SCENE      CAPELLA_C13_SM_SICD_HH_20260204114511_20260204114516 (Niscemi R-A)
#   MULTIRTC_SITE  /data/external/multirtc-site   (prepended to PYTHONPATH)
set -uo pipefail

REIN=${REIN:-/data/capella_reinforcement}
SCRATCH=${SCRATCH:-$REIN/scratch}
PYTHON=${PYTHON:-python}
TIME=${TIME-/usr/bin/time -v}
RTC_SCENE=${RTC_SCENE:-CAPELLA_C13_SM_SICD_HH_20260204114511_20260204114516}
MULTIRTC_SITE=${MULTIRTC_SITE:-/data/external/multirtc-site}
RTC_VARIANTS=(stock matched matched-tfix-rfix)
SCENES=(
    CAPELLA_C13_SM_SICD_HH_20260204114511_20260204114516
    CAPELLA_C13_SM_SICD_HH_20260207104155_20260207104201
    CAPELLA_C13_SM_SICD_HH_20260204183253_20260204183257
    CAPELLA_C13_SM_SICD_HH_20260207172937_20260207172942
    CAPELLA_C15_SM_SICD_HH_20260627144157_20260627144201
)
NISCEMI_BBOX="14.20 37.00 14.57 37.29"
YUMARE_BBOX="-68.80 10.45 -68.55 10.75"

die() { echo "FAIL $*" >&2; exit 1; }
short_id() { local t=${1#*_HH_}; echo "${t%%_*}"; }
site() { case "$1" in *_C15_*) echo yumare ;; *) echo niscemi ;; esac; }
epsg() { case "$1" in yumare) echo 32619 ;; *) echo 32633 ;; esac; }
bbox() { case "$1" in yumare) echo "$YUMARE_BBOX" ;; *) echo "$NISCEMI_BBOX" ;; esac; }
title() { case "$1" in yumare) echo Yumare ;; *) echo Niscemi ;; esac; }

dem() {
    mkdir -p "$REIN/logs" || die "mkdir"
    for s in niscemi yumare; do
        # shellcheck disable=SC2046  # bbox is four separate numbers
        "$PYTHON" fetch/fetch_dem_bbox.py --bbox $(bbox $s) --out "$REIN/dem_$s.tif" \
            > "$REIN/logs/dem_$s.log" 2>&1 || die "dem $s (see $REIN/logs/dem_$s.log)"
        echo "DEM-OK $s"
    done
}

convert() {  # $1 = dn | beta0
    local mode=$1 dir=$REIN/rslc
    [ "$mode" = beta0 ] && dir=$REIN/rslc_beta0
    mkdir -p "$dir" "$REIN/logs" || die "mkdir"
    for g in "${SCENES[@]}"; do
        local id; id=$(short_id "$g")
        "$PYTHON" tools/sicd_to_nisar_rslc.py "$REIN/$g.ntf" "$dir/$id.h5" \
            --radiometry "$mode" --overwrite \
            > "$REIN/logs/convert_${mode}_$id.log" 2>&1 \
            || die "convert-$mode $id (see $REIN/logs/convert_${mode}_$id.log)"
        echo "CONVERT-OK $mode $id"
    done
}

geometry() {
    mkdir -p "$REIN/rslc" "$REIN/logs" || die "mkdir"
    for g in "${SCENES[@]}"; do
        local id; id=$(short_id "$g")
        "$PYTHON" tools/sicd_rslc_geometry_check.py isce3 "$REIN/rslc/$id.h5" \
            --out "$REIN/rslc/geom_isce3_$id.json" > "$REIN/logs/geom_isce3_$id.log" 2>&1 \
            || die "geometry-check $id (see $REIN/logs/geom_isce3_$id.log)"
        echo "GEOMETRY-OK $id"
    done
}

gcov() {
    mkdir -p "$REIN/configs" || die "mkdir"
    for g in "${SCENES[@]}"; do
        local id s name out scr cfg
        id=$(short_id "$g"); s=$(site "$g"); name=gcov_$id
        out=$REIN/gcov/$id; scr=$SCRATCH/gcov_$id; cfg=$REIN/configs/$name.yaml
        mkdir -p "$out" "$scr" || die "mkdir $out"
        sed -e "s|{name}|$name|g" -e "s|{rslc}|$REIN/rslc_beta0/$id.h5|" \
            -e "s|{dem}|$REIN/dem_$s.tif|" -e "s|{out}|$out|g" -e "s|{scratch}|$scr|" \
            -e "s|{epsg}|$(epsg "$s")|" \
            -e "s|{dem_desc}|Copernicus GLO-30 over $(title "$s"), bbox $(bbox "$s") (fetch/fetch_dem_bbox.py, dem_stitcher, ellipsoidal heights, EPSG:4326)|" \
            configs/gcov_capella_template.yaml > "$cfg" || die "render $cfg"
        grep -q '{[a-z_]*}' "$cfg" && die "unrendered placeholder in $cfg"
        # shellcheck disable=SC2086  # TIME is a command plus its flag, or empty
        $TIME "$PYTHON" -m nisar.workflows.gcov "$cfg" > "$out/console.log" 2>&1 \
            || die "gcov $id (see $out/console.log)"
        echo "GCOV-OK $id"
    done
}

multirtc() {
    export PYTHONPATH="$MULTIRTC_SITE${PYTHONPATH:+:$PYTHONPATH}"
    local id s; id=$(short_id "$RTC_SCENE"); s=$(site "$RTC_SCENE")
    for v in "${RTC_VARIANTS[@]}"; do
        local w="$REIN/multirtc/$id/$v"
        mkdir -p "$w" || die "mkdir $w"
        "$PYTHON" tools/multirtc_capella_rtc.py rtc "$REIN/$RTC_SCENE.ntf" --variant "$v" \
            --dem "$REIN/dem_$s.tif" --resolution 5 --work-dir "$w" \
            > "$w/console.log" 2>&1 || die "multirtc $v (see $w/console.log)"
        echo "MULTIRTC-OK $id $v"
    done
}

rtc_compare() {
    local id; id=$(short_id "$RTC_SCENE")
    mkdir -p "$REIN/rtc_compare" || die "mkdir"
    for v in "${RTC_VARIANTS[@]}"; do
        "$PYTHON" tools/compare_rtc.py --gcov "$REIN/gcov/$id/gcov_$id.h5" \
            --rslc "$REIN/rslc_beta0/$id.h5" --multirtc-run "$REIN/multirtc/$id/$v" \
            --out "$REIN/rtc_compare/${id}_$v.json" > /dev/null || die "rtc-compare $v"
        echo "COMPARE-OK $id $v"
    done
}

case "${1:-}" in
    dem)           dem ;;
    convert)       convert dn ;;
    convert-beta0) convert beta0 ;;
    geometry)      geometry ;;
    gcov)          gcov ;;
    multirtc)      multirtc ;;
    rtc-compare)   rtc_compare ;;
    *) echo "usage: $0 dem | convert | convert-beta0 | geometry | gcov | multirtc | rtc-compare" >&2; exit 2 ;;
esac
