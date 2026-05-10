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
        # Workflow dispatch is decided by inspecting the runconfig: each
        # config sets `runconfig.name: <workflow>`.
        # Also pre-create scratch/product paths — workflows assume they exist.
        wf="$(python -c "import yaml; d=yaml.safe_load(open('${cfg}')); print(d['runconfig']['name'])")"
        python -c "
import os, yaml
d = yaml.safe_load(open('${cfg}'))
g = d['runconfig']['groups'].get('product_path_group') or {}
for k in ('scratch_path', 'product_path', 'sas_output_file'):
    v = g.get(k)
    if isinstance(v, str) and v.startswith('/'):
        # If sas_output_file points at a file (has an extension), mkdir its parent.
        # If it's a dir (COMPASS style), mkdir the path itself.
        target = v if (k != 'sas_output_file' or os.path.splitext(v)[1] == '') else os.path.dirname(v)
        os.makedirs(target, exist_ok=True)
"
        # Resolve dispatch.
        case "${wf}" in
            focus|gslc|gcov|insar)
                cmd=(python -m "nisar.workflows.${wf}" "${cfg}") ;;
            cslc_s1_workflow_default)
                # COMPASS s1_cslc.py requires --grid {radar,geo}. We use radar
                # mode because that path lights up isce3's GPU primitives
                # (Rdr2Geo, Geo2Rdr, ResampSlc); geo mode dispatches to the
                # CPU-only isce3.geocode.geocode_slc.
                cmd=(s1_cslc.py "${cfg}" --grid radar) ;;
            crossmul_s1)
                # Direct primitive call. Inputs are read from the runconfig
                # by the script itself.
                cmd=(python "${BENCH_ROOT}/scripts/run_crossmul.py" --config "${cfg}") ;;
            *)
                echo "ERROR: unknown workflow '${wf}' in ${cfg}" >&2
                exit 2 ;;
        esac
        for i in $(seq 1 "${repeats}"); do
            tag="$(basename "${prefix}")_${path}_${i}"
            echo ">>> ${tag} (workflow=${wf})"
            timed_run "${run_dir}" "${tag}" "${cmd[@]}"
        done
    done
done

echo "done. summarise with: tools/parse_timing.py --logs ${run_dir}"
