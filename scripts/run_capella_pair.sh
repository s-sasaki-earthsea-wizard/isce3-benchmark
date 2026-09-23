#!/usr/bin/env bash
#
# Run insar.py (RIFG) on the converted Capella Mexico City pair, once per
# crossmul-flatten variant, from configs/insar_capella_mexico_city_template.yaml.
# Same container / mount scheme as scripts/run_alos2_network.sh (one /out and
# one /scratch per run, VRAM gate before starting), but against the develop
# isce3 build (ISCE3_BUILD_DIR from .env) -- see the template header.
#
# Usage:
#   scripts/run_capella_pair.sh [--dry-run] [--keep-scratch] flat|noflat [...]
#
# Log lines: RENDER / RUN / OK / FAIL / SKIP / VRAM-WAIT / VRAM-OK.

set -uo pipefail
cd "$(dirname "$0")/.."
BENCH=$(pwd)

TEMPLATE=configs/insar_capella_mexico_city_template.yaml
CONFIG_DIR=${CONFIG_DIR:-configs/capella_mexico_city}
OUT_ROOT=${OUT_ROOT:-$BENCH/data/capella_mexico_city/rifg}
SCRATCH_ROOT=${SCRATCH_ROOT:-$HOME/scratch/capella}
MIN_FREE_VRAM_MB=${MIN_FREE_VRAM_MB:-10000}
VRAM_WAIT_MAX_S=${VRAM_WAIT_MAX_S:-43200}
VRAM_POLL_S=${VRAM_POLL_S:-120}

DRY_RUN=0; KEEP_SCRATCH=1; VARIANTS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)      DRY_RUN=1 ;;
        --keep-scratch) KEEP_SCRATCH=1 ;;
        --rm-scratch)   KEEP_SCRATCH=0 ;;
        flat|noflat)    VARIANTS+=("$1") ;;
        -h|--help)      sed -n '2,12p' "$0"; exit 0 ;;
        *)              echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[ ${#VARIANTS[@]} -eq 0 ] && { echo "need at least one variant: flat|noflat" >&2; exit 2; }

wait_for_vram() {
    local name=$1 waited=0 free used total
    while :; do
        read -r used total < <(nvidia-smi --query-gpu=memory.used,memory.total \
            --format=csv,noheader,nounits 2>/dev/null | tr ',' ' ')
        [ -z "${total:-}" ] && { echo "WARN  $name: no nvidia-smi, skipping the VRAM gate" >&2; return 0; }
        free=$((total - used))
        if [ "$free" -ge "$MIN_FREE_VRAM_MB" ]; then
            [ "$waited" -gt 0 ] && echo "VRAM-OK $name after ${waited}s (${free} MiB free)"
            return 0
        fi
        [ "$waited" -eq 0 ] && echo "VRAM-WAIT $name: ${free} MiB free (need $MIN_FREE_VRAM_MB)"
        [ "$waited" -ge "$VRAM_WAIT_MAX_S" ] && { echo "ABORT $name: VRAM wait timed out" >&2; return 1; }
        sleep "$VRAM_POLL_S"; waited=$((waited + VRAM_POLL_S))
    done
}

mkdir -p "$BENCH/$CONFIG_DIR" "$OUT_ROOT" "$SCRATCH_ROOT"
failed=0
for v in "${VARIANTS[@]}"; do
    case "$v" in flat) flatten=true ;; noflat) flatten=false ;; esac
    name=rifg_capella_mexico_city_20240626_20240629_$v
    cfg=$BENCH/$CONFIG_DIR/insar_$name.yaml
    sed -e "s/{name}/$name/" -e "s/{flatten}/$flatten/" "$TEMPLATE" > "$cfg"
    echo "RENDER $cfg (flatten=$flatten)"

    out=$OUT_ROOT/$name; scratch=$SCRATCH_ROOT/$name
    if [ -f "$out/.complete" ]; then echo "SKIP  $name (already complete)"; continue; fi
    wait_for_vram "$name" || { failed=$((failed + 1)); continue; }
    echo "RUN   $name  $(date -Is)"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "      docker compose run --rm -T -v $out:/out -v $scratch:/scratch dev" \
             "python3 -m nisar.workflows.insar /work/$CONFIG_DIR/insar_$name.yaml --restart"
        continue
    fi
    mkdir -p "$out" "$scratch"; rm -f "$out/.complete"
    t0=$(date +%s)
    ( docker compose run --rm -T -v "$out:/out" -v "$scratch:/scratch" \
        dev /usr/bin/time -v python3 -m nisar.workflows.insar \
            "/work/$CONFIG_DIR/insar_$name.yaml" --restart
    ) > "$out/console.log" 2>&1
    rc=$?; secs=$(( $(date +%s) - t0 ))
    size=$(stat -c%s "$out/product.h5" 2>/dev/null || echo 0)
    if [ "$rc" -eq 0 ] && [ "$size" -gt 1000000 ] && ! grep -q "Traceback" "$out/console.log"; then
        echo "OK    $name  rc=$rc  ${secs}s  product $((size / 1000000)) MB  $(date -Is)"
        date -Is > "$out/.complete"
        if [ "$KEEP_SCRATCH" -eq 0 ]; then
            docker compose run --rm -T -v "$SCRATCH_ROOT:/scratch_root" dev \
                rm -rf "/scratch_root/$name" >/dev/null 2>&1 || echo "WARN  scratch cleanup failed" >&2
        fi
    else
        echo "FAIL  $name  rc=$rc  ${secs}s  product ${size} B  (log $out/console.log)" >&2
        failed=$((failed + 1))
    fi
done
echo "DONE failed=$failed $(date -Is)"
[ "$failed" -eq 0 ]
