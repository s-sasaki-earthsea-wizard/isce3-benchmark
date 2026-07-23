#!/usr/bin/env bash
# py-spy sampling profile of a single workflow run. Captures CPU-side hotspots
# (Python frames + native frames) — complementary to Nsight which sees GPU.
#
# Usage:
#   scripts/run_profile_pyspy.sh [path/to/runconfig.yaml] [compass_grid]
#   compass_grid: radar (default) or geo
set -euo pipefail
source "$(dirname "$0")/_common.sh"

cfg="${1:-${BENCH_ROOT}/configs/smoke_ree_rslc_cpu.yaml}"
compass_grid="${2:-radar}"
if [ ! -f "${cfg}" ]; then
    echo "config not found: ${cfg}" >&2; exit 1
fi

ensure_runconfig_paths "${cfg}"
mapfile -t cmd < <(dispatch_workflow "${cfg}" "${compass_grid}")

tag="pyspy"
if [ "${compass_grid}" = "geo" ]; then tag="pyspy_geo"; fi
run_dir="$(new_run_dir "${tag}")"
record_provenance "${run_dir}"

# --native (C-frame attribution) is opt-in via PYSPY_NATIVE=1. It is more
# informative for pybind11/numpy hotspots, but interferes with h5py's GC
# in some COMPASS configs (HDF5 "Software caused connection abort" on
# context-manager exit). Default off — Python attribution is enough for
# the I/O-vs-compute determination.
native_arg=()
if [ "${PYSPY_NATIVE:-0}" = "1" ]; then
    native_arg=(--native)
fi

# Output format is opt-in via PYSPY_FORMAT (default: flamegraph SVG).
# "raw" emits collapsed stacks with sample counts — the easiest form to
# aggregate quantitatively (compute-vs-I/O attribution) with plain scripts.
fmt="${PYSPY_FORMAT:-flamegraph}"
case "${fmt}" in
    flamegraph) ext="svg" ;;
    speedscope) ext="json" ;;
    raw)        ext="collapsed.txt" ;;
    *) echo "unsupported PYSPY_FORMAT: ${fmt}" >&2; exit 1 ;;
esac

py-spy record \
    --output "${run_dir}/pyspy.${ext}" \
    --format "${fmt}" \
    --rate 100 \
    --subprocesses \
    "${native_arg[@]}" \
    -- "${cmd[@]}" \
    > >(tee "${run_dir}/run.log") \
    2> >(tee "${run_dir}/run.err" >&2)

echo "profile (${fmt}): ${run_dir}/pyspy.${ext}"
