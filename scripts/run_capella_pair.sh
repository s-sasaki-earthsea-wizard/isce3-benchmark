#!/usr/bin/env bash
#
# Run insar.py (RIFG) on a converted Capella pair, once per crossmul-flatten
# variant. --pair mexico_city (default) renders the original
# configs/insar_capella_mexico_city_template.yaml unchanged; the reinforcement
# pairs (niscemi_ra, niscemi_ld) render configs/insar_capella_template.yaml,
# which has the same processing settings plus per-pair placeholders.
# The workflow is entered through scripts/run_insar_xband.py, which relaxes the
# InSAR writer's L/S-band-only check (X-band would otherwise stop the run at
# prepare_insar_hdf5); everything else is the stock nisar.workflows.insar.
# Same container / mount scheme as scripts/run_alos2_network.sh (one /out and
# one /scratch per run, VRAM gate before starting), but against the develop
# isce3 build (ISCE3_BUILD_DIR from .env) -- see the template header.
#
# Usage:
#   scripts/run_capella_pair.sh [--pair NAME] [--dry-run] [--keep-scratch] flat|noflat [...]
#
# Log lines: RENDER / RUN / OK / FAIL / SKIP / VRAM-WAIT / VRAM-OK.

set -uo pipefail
cd "$(dirname "$0")/.."
BENCH=$(pwd)

SCRATCH_ROOT=${SCRATCH_ROOT:-$HOME/scratch/capella}
MIN_FREE_VRAM_MB=${MIN_FREE_VRAM_MB:-10000}
VRAM_WAIT_MAX_S=${VRAM_WAIT_MAX_S:-43200}
VRAM_POLL_S=${VRAM_POLL_S:-120}

DRY_RUN=0; KEEP_SCRATCH=1; VARIANTS=(); PAIR=mexico_city
while [ $# -gt 0 ]; do
    case "$1" in
        --pair)         PAIR=${2:?}; shift ;;
        --dry-run)      DRY_RUN=1 ;;
        --keep-scratch) KEEP_SCRATCH=1 ;;
        --rm-scratch)   KEEP_SCRATCH=0 ;;
        flat|noflat)    VARIANTS+=("$1") ;;
        -h|--help)      sed -n '2,14p' "$0"; exit 0 ;;
        *)              echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[ ${#VARIANTS[@]} -eq 0 ] && { echo "need at least one variant: flat|noflat" >&2; exit 2; }

# Per-pair inputs. REF/SEC/DEM are container paths; TAG is the run-name stem.
REIN=/data/capella_reinforcement
NISCEMI_DEM_DESC="Copernicus DEM GLO-30 (1 arcsec) over Niscemi, bbox 14.20 37.00 14.57 37.29, staged with fetch/fetch_dem_bbox.py (dem_stitcher, ellipsoidal heights)."
NISCEMI_HEIGHTS="[-100, 0, 100, 200, 300, 400, 500, 600, 800, 1000]"
case "$PAIR" in
    mexico_city)
        TEMPLATE=configs/insar_capella_mexico_city_template.yaml
        CONFIG_DIR=${CONFIG_DIR:-configs/capella_mexico_city}
        OUT_ROOT=${OUT_ROOT:-$BENCH/data/capella_mexico_city/rifg}
        TAG=rifg_capella_mexico_city_20240626_20240629 ;;
    niscemi_ra|niscemi_ld)
        TEMPLATE=configs/insar_capella_template.yaml
        CONFIG_DIR=${CONFIG_DIR:-configs/capella_reinforcement}
        OUT_ROOT=${OUT_ROOT:-$BENCH/data/capella_reinforcement/rifg}
        DEM=$REIN/dem_niscemi.tif; DEM_DESC=$NISCEMI_DEM_DESC; EPSG=32633; HEIGHTS=$NISCEMI_HEIGHTS
        if [ "$PAIR" = niscemi_ra ]; then
            REF=$REIN/rslc/20260204114511.h5; SEC=$REIN/rslc/20260207104155.h5
            TAG=rifg_capella_niscemi_ra_20260204_20260207
        else
            REF=$REIN/rslc/20260204183253.h5; SEC=$REIN/rslc/20260207172937.h5
            TAG=rifg_capella_niscemi_ld_20260204_20260207
        fi ;;
    *) echo "unknown pair: $PAIR (mexico_city | niscemi_ra | niscemi_ld)" >&2; exit 2 ;;
esac

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
    name=${TAG}_$v
    cfg=$BENCH/$CONFIG_DIR/insar_$name.yaml
    sed -e "s|{name}|$name|" -e "s|{flatten}|$flatten|" \
        -e "s|{ref}|${REF:-}|" -e "s|{sec}|${SEC:-}|" -e "s|{dem}|${DEM:-}|" \
        -e "s|{dem_desc}|${DEM_DESC:-}|" -e "s|{epsg}|${EPSG:-}|" -e "s|{heights}|${HEIGHTS:-}|" \
        "$TEMPLATE" > "$cfg"
    if grep -q '{[a-z_]*}' "$cfg"; then echo "FAIL  $name: unrendered placeholder in $cfg" >&2; exit 1; fi
    echo "RENDER $cfg (flatten=$flatten)"

    out=$OUT_ROOT/$name; scratch=$SCRATCH_ROOT/$name
    if [ -f "$out/.complete" ]; then echo "SKIP  $name (already complete)"; continue; fi
    wait_for_vram "$name" || { failed=$((failed + 1)); continue; }
    echo "RUN   $name  $(date -Is)"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "      docker compose run --rm -T -v $out:/out -v $scratch:/scratch dev" \
             "python3 /work/scripts/run_insar_xband.py /work/$CONFIG_DIR/insar_$name.yaml --restart"
        continue
    fi
    mkdir -p "$out" "$scratch"; rm -f "$out/.complete"
    t0=$(date +%s)
    ( docker compose run --rm -T -v "$out:/out" -v "$scratch:/scratch" \
        dev /usr/bin/time -v python3 /work/scripts/run_insar_xband.py \
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
