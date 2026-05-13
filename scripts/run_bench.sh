#!/usr/bin/env bash
# Run benchmark scenarios. Each scenario is a (CPU, GPU) pair of runconfigs
# under configs/. Results land in ${BENCH_LOG_DIR}/<timestamp>_<tag>/.
#
# Usage:
#   scripts/run_bench.sh smoke      # tiny REE end-to-end CPU+GPU
#   scripts/run_bench.sh full       # all scenarios in configs/
set -euo pipefail
source "$(dirname "$0")/_common.sh"

mode="${1:-smoke}"

case "${mode}" in
  smoke)
    pairs=(
      "configs/smoke_ree_rslc"
    )
    repeats=1
    ;;
  s1)
    # Sentinel-1 Boso bench: COMPASS CSLC (ref + sec) → direct crossmul.
    # The crossmul pair is rendered after CSLC outputs are observed; if it
    # is missing the loop just skips it.
    pairs=(
      "configs/insar_s1_boso_cslc"
      "configs/insar_s1_boso_cslc_sec"
      "configs/insar_s1_boso_crossmul"
    )
    repeats=3
    ;;
  full)
    pairs=(
      "configs/smoke_ree_rslc"
      "configs/insar_s1_boso_cslc"
      "configs/insar_s1_boso_cslc_sec"
      "configs/insar_s1_boso_crossmul"
    )
    repeats=3
    ;;
  *)
    echo "unknown mode: ${mode}" >&2; exit 2 ;;
esac

run_dir="$(new_run_dir "${mode}")"
record_provenance "${run_dir}"
echo "logging to: ${run_dir}"

for prefix in "${pairs[@]}"; do
    for path in cpu gpu; do
        cfg="${BENCH_ROOT}/${prefix}_${path}.yaml"
        if [ ! -f "${cfg}" ]; then
            echo "SKIP: ${cfg} not found"
            continue
        fi
        # Pre-create scratch/product paths — workflows assume they exist.
        ensure_runconfig_paths "${cfg}"

        # Pick COMPASS grid mode by config name. Default-radar matches the
        # historical Stage 1 behaviour (lights up isce3 GPU primitives).
        # `*_geo_*` configs profile the geo-mode kernel (isce3.geocode.geocode_slc).
        case "${prefix}" in
            *_geo) compass_grid="geo" ;;
            *)     compass_grid="radar" ;;
        esac
        mapfile -t cmd < <(dispatch_workflow "${cfg}" "${compass_grid}")
        wf="$(python -c "import yaml; print(yaml.safe_load(open('${cfg}'))['runconfig']['name'])")"

        for i in $(seq 1 "${repeats}"); do
            tag="$(basename "${prefix}")_${path}_${i}"
            echo ">>> ${tag} (workflow=${wf}, grid=${compass_grid})"
            timed_run "${run_dir}" "${tag}" "${cmd[@]}"
        done
    done
done

echo "done. summarise with: tools/parse_timing.py --logs ${run_dir}"
