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
