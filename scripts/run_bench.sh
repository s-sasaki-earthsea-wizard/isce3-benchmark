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
  full)
    # Populated as we add scenarios.
    pairs=(
      "configs/smoke_ree_rslc"
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
        for i in $(seq 1 "${repeats}"); do
            tag="$(basename "${prefix}")_${path}_${i}"
            echo ">>> ${tag}"
            # Workflow dispatch is decided by inspecting the runconfig: each
            # config sets `runconfig.name: <workflow>` (focus, gslc, gcov, insar).
            wf="$(python -c "import yaml,sys; d=yaml.safe_load(open('${cfg}')); print(d['runconfig']['name'])")"
            timed_run "${run_dir}" "${tag}" \
                python -m "nisar.workflows.${wf}" "${cfg}"
        done
    done
done

echo "done. summarise with: tools/parse_timing.py --logs ${run_dir}"
