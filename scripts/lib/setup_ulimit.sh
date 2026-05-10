# Cap virtual address space at <ULIMIT_FRACTION>% of physical RAM.
# Default 80%. Source from each run_*.sh.
#
# Why this safety net exists: a sibling project (mintpy-benchmark) had a
# 2026-05-02 incident where torch.profiler with `with_stack=True` grew host
# RSS to 94.9 GiB on a 93 GiB physical host and required hard reboot.
# isce3 / GDAL / cuFFT workloads on full-subswath SAFE bursts have their own
# memory profile that can also exceed RAM under py-spy / nsys overhead.
#
# Why /proc/meminfo and not `free -h`:
#   - MemTotal is in KiB (matches `ulimit -v`'s default unit, no conversion)
#   - language-independent (free's labels can vary by locale)
#   - present even in containers where `free` may be missing
#
# When inside a Docker container, /proc/meminfo reports the host's MemTotal
# unless the container itself has been started with --memory. Tighter caps
# can be set via ULIMIT_FRACTION env var.

mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
fraction="${ULIMIT_FRACTION:-80}"
limit_kb=$(( mem_kb * fraction / 100 ))

ulimit -v "${limit_kb}"

echo "[setup_ulimit] virtual memory capped at $((limit_kb / 1024 / 1024)) GiB" \
     "(${fraction}% of $((mem_kb / 1024 / 1024)) GiB physical)"
