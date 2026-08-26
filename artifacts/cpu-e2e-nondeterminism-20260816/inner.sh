#!/bin/bash
# Runs INSIDE the isce3-benchmark dev container.
# The image entrypoint wires PYTHONPATH/LD_LIBRARY_PATH from
# /opt/isce3-build/install; we prepend a copy-overlay so the ONLY
# variable between control and treat is the utils.py content
# (same discipline as the 2026-08-13 GPU E2E A/B).
set -euo pipefail

cp -r /opt/isce3-build/install/packages /tmp/ov
if [ "${AB_MODE:?}" = "treat" ]; then
    cp /ab/patched_utils.py /tmp/ov/nisar/products/insar/utils.py
fi
export PYTHONPATH=/tmp/ov:${PYTHONPATH:-}

UTILS=$(python3 -c 'import nisar.products.insar.utils as u; print(u.__file__)')
echo "utils: $UTILS"
if [ "$UTILS" != "/tmp/ov/nisar/products/insar/utils.py" ]; then
    echo "FATAL: overlay not resolved (got $UTILS)" >&2
    exit 9
fi
echo "patched_marker_count=$(grep -c _subswath_numbers /tmp/ov/nisar/products/insar/utils.py || true)"
echo "ab_mode=$AB_MODE run_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

exec /usr/bin/time -v python3 -m nisar.workflows.insar \
    /configs/insar_gunw_ASC139_019_20260705_20260717.yaml --restart
