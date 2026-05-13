#!/usr/bin/env bash
# Shared helpers for benchmark harness scripts. Source from each script.
set -euo pipefail

# Resolve repo root (script dir's parent).
BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BENCH_ROOT

# Defaults — overridable via env.
: "${BENCH_LOG_DIR:=${BENCH_ROOT}/logs_$(hostname -s)}"
: "${BENCH_DATA_DIR:=${BENCH_ROOT}/data}"

# Per-run subdirectory keyed by timestamp + tag.
new_run_dir() {
    local tag="${1:-run}"
    local stamp
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local dir="${BENCH_LOG_DIR}/${stamp}_${tag}"
    mkdir -p "${dir}"
    echo "${dir}"
}

# Capture host + isce3 build provenance into <dir>/provenance.txt.
record_provenance() {
    local dir="$1"
    {
        echo "# captured: $(date -u +%FT%TZ)"
        echo "# host: $(hostname)"
        echo
        echo "## kernel"
        uname -a
        echo
        echo "## cpu"
        lscpu | grep -E '^(Model name|CPU\(s\)|Thread|Socket|L3 cache)' || true
        echo
        echo "## memory"
        free -h
        echo
        echo "## gpu"
        nvidia-smi -L 2>/dev/null || echo "(no nvidia-smi)"
        nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>/dev/null || true
        echo
        echo "## cuda"
        nvcc --version 2>/dev/null || echo "(no nvcc)"
        echo
        echo "## isce3 build"
        if [ -d /opt/isce3-src/.git ]; then
            (cd /opt/isce3-src && git rev-parse HEAD)
            (cd /opt/isce3-src && git status --porcelain | head -5)
        fi
        echo
        echo "## env"
        env | grep -E '^(ISCE|OMP|MKL|NVIDIA|CUDA)' | sort
    } > "${dir}/provenance.txt"
}

# Wrap a command with /usr/bin/time -v and tee its stdout/stderr to <dir>/<tag>.log.
timed_run() {
    local dir="$1"; shift
    local tag="$1"; shift
    /usr/bin/time -v -o "${dir}/${tag}.time" -- "$@" \
        > >(tee "${dir}/${tag}.log") \
        2> >(tee "${dir}/${tag}.err" >&2)
}

# Read a runconfig and pre-create scratch / product directories the workflow
# expects to exist. Used by run_bench.sh and the profile scripts.
ensure_runconfig_paths() {
    local cfg="$1"
    python - "$cfg" <<'PY'
import os, sys, yaml
cfg = sys.argv[1]
d = yaml.safe_load(open(cfg))
g = d['runconfig']['groups'].get('product_path_group') or {}
for k in ('scratch_path', 'product_path', 'sas_output_file'):
    v = g.get(k)
    if isinstance(v, str) and v.startswith('/'):
        # sas_output_file may be a file path; mkdir its parent in that case.
        target = v if (k != 'sas_output_file' or os.path.splitext(v)[1] == '') else os.path.dirname(v)
        os.makedirs(target, exist_ok=True)
PY
}

# Resolve the launch command for a given runconfig. Echoes the command words
# one-per-line; callers wrap with `mapfile -t cmd < <(dispatch_workflow ...)`.
# The decision is keyed off `runconfig.name` (the workflow identifier) plus,
# for COMPASS, the grid kind we want — radar mode lights up isce3's GPU
# primitives (Rdr2Geo / Geo2Rdr / ResampSlc) while geo mode dispatches to
# the CPU-only isce3.geocode.geocode_slc kernel.
#
# Usage: dispatch_workflow <runconfig.yaml> [compass_grid]
#   compass_grid defaults to "radar" (matches run_bench.sh history).
dispatch_workflow() {
    local cfg="$1"
    local compass_grid="${2:-radar}"
    local wf
    wf="$(python -c "import yaml; print(yaml.safe_load(open('${cfg}'))['runconfig']['name'])")"
    case "${wf}" in
        focus|gslc|gcov|insar)
            printf '%s\n' python -m "nisar.workflows.${wf}" "${cfg}" ;;
        cslc_s1_workflow_default)
            printf '%s\n' s1_cslc.py "${cfg}" --grid "${compass_grid}" ;;
        crossmul_s1)
            printf '%s\n' python "${BENCH_ROOT}/scripts/run_crossmul.py" --config "${cfg}" ;;
        *)
            echo "ERROR: unknown workflow '${wf}' in ${cfg}" >&2
            return 2 ;;
    esac
}
