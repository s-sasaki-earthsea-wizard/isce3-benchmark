#!/usr/bin/env bash
#
# Run the self-run GUNW workflow over every pair of the ALOS-2 Kujukuri
# closure network. Modelled on nisar-displacement/scripts/run_gunw_batch.sh
# (same container, same isce3 v0.25.16 build, same per-pair mount scheme):
# the runconfigs all write to /out and /scratch, so the pair separation
# lives entirely in the bind mounts.
#
# Strictly sequential. Scratch is deleted only after a pair succeeds, so a
# failure stays inspectable. Skips on the .complete marker, never on the
# product alone (product.h5 is written incrementally).
#
# Usage:
#   scripts/run_alos2_network.sh [--dry-run] [--only <ref>_<sec>] \
#                                [--keep-going] [--max-pairs N]
#
# Environment overrides: SCRATCH_ROOT, ISCE3_SRC, ISCE3_BUILD_DIR,
# CONFIG_DIR, OUT_ROOT, MIN_SCRATCH_GB.
#
# Log lines: RUN / OK / FAIL / SKIP / REDO / ABORT / BATCH-COMPLETE.

set -uo pipefail
cd "$(dirname "$0")/.."
BENCH=$(pwd)

SCRATCH_ROOT=${SCRATCH_ROOT:-$HOME/scratch/alos2}
ISCE3_SRC=${ISCE3_SRC:-/mnt/nas/Projects/third-party-projects/isce3-v0.25.16}
ISCE3_BUILD_DIR=${ISCE3_BUILD_DIR:-./isce3-build-v0.25.16}
CONFIG_DIR=${CONFIG_DIR:-configs/alos2_kujukuri}
OUT_ROOT=${OUT_ROOT:-$BENCH/data/ALOS2-kujukuri/gunw}
MIN_SCRATCH_GB=${MIN_SCRATCH_GB:-100}

DRY_RUN=0; KEEP_GOING=0; ONLY=; MAX_PAIRS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --keep-going) KEEP_GOING=1 ;;
        --only)       ONLY=${2:?--only needs a <ref>_<sec> tag}; shift ;;
        --max-pairs)  MAX_PAIRS=${2:?}; shift ;;
        -h|--help)    sed -n '2,22p' "$0"; exit 0 ;;
        *)            echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

shopt -s nullglob
configs=("$BENCH/$CONFIG_DIR"/insar_gunw_*.yaml)
shopt -u nullglob
if [ ${#configs[@]} -eq 0 ]; then
    echo "no runconfigs in $CONFIG_DIR (run tools/make_alos2_network.py)" >&2
    exit 1
fi

mkdir -p "$SCRATCH_ROOT" "$OUT_ROOT"
echo "BATCH-START $(date -Is) pairs=${#configs[@]} isce3=$ISCE3_SRC build=$ISCE3_BUILD_DIR"
failed=0; ran=0
for cfg in "${configs[@]}"; do
    base=$(basename "$cfg" .yaml)
    name=${base#insar_}                 # gunw_alos2_kujukuri_<ref>_<sec>
    tag=${name#gunw_alos2_kujukuri_}    # <ref>_<sec>
    if [ -n "$ONLY" ] && [ "$tag" != "$ONLY" ]; then continue; fi
    if [ "$MAX_PAIRS" -gt 0 ] && [ "$ran" -ge "$MAX_PAIRS" ]; then break; fi

    out=$OUT_ROOT/$name
    scratch=$SCRATCH_ROOT/$name
    if [ -f "$out/.complete" ]; then echo "SKIP  $name (already complete)"; continue; fi
    if [ -e "$out/product.h5" ]; then echo "REDO  $name (incomplete product from an earlier run)"; fi

    avail_gb=$(df -BG --output=avail "$SCRATCH_ROOT" | tail -1 | tr -dc '0-9')
    if [ "${avail_gb:-0}" -lt "$MIN_SCRATCH_GB" ]; then
        echo "ABORT $name: only ${avail_gb} GB free on $SCRATCH_ROOT (need $MIN_SCRATCH_GB)" >&2
        exit 1
    fi

    echo "RUN   $name  $(date -Is)  (scratch ${avail_gb} GB free)"
    ran=$((ran + 1))
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "      docker compose run --rm -T -v $out:/out -v $scratch:/scratch dev" \
             "python3 -m nisar.workflows.insar /work/$CONFIG_DIR/$base.yaml --restart"
        continue
    fi

    mkdir -p "$out" "$scratch"
    rm -f "$out/.complete"
    t0=$(date +%s)
    ( ISCE3_SRC=$ISCE3_SRC ISCE3_BUILD_DIR=$ISCE3_BUILD_DIR \
        docker compose run --rm -T \
            -v "$out:/out" \
            -v "$scratch:/scratch" \
            dev /usr/bin/time -v python3 -m nisar.workflows.insar \
                "/work/$CONFIG_DIR/$base.yaml" --restart
    ) > "$out/console.log" 2>&1
    rc=$?
    secs=$(( $(date +%s) - t0 ))

    size=$(stat -c%s "$out/product.h5" 2>/dev/null || echo 0)
    if [ "$rc" -eq 0 ] && [ "$size" -gt 1000000 ] && ! grep -q "Traceback" "$out/console.log"; then
        echo "OK    $name  rc=$rc  ${secs}s  product $((size / 1000000)) MB  $(date -Is)"
        date -Is > "$out/.complete"
        rm -rf "$scratch"
    else
        echo "FAIL  $name  rc=$rc  ${secs}s  product ${size} B  $(date -Is)" \
             "(scratch kept at $scratch, log at $out/console.log)" >&2
        failed=$((failed + 1))
        [ "$KEEP_GOING" -eq 0 ] && exit 1
    fi
done
echo "BATCH-COMPLETE failed=$failed ran=$ran $(date -Is)"
[ "$failed" -eq 0 ]
