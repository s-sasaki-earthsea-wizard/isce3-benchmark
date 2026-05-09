#!/usr/bin/env bash
# Activate the isce3 micromamba env, expose the freshly built isce3 install on
# PYTHONPATH, then exec the user's command.
set -euo pipefail

eval "$(micromamba shell hook --shell bash)"
micromamba activate isce3

# If isce3 has been built into /opt/isce3-build/install via scripts/build_isce3.sh,
# put it ahead of any pip-installed isce3 in the env.
ISCE3_INSTALL=/opt/isce3-build/install
if [ -d "${ISCE3_INSTALL}/packages" ]; then
    export PYTHONPATH="${ISCE3_INSTALL}/packages:${PYTHONPATH:-}"
    export LD_LIBRARY_PATH="${ISCE3_INSTALL}/lib:${ISCE3_INSTALL}/lib64:${LD_LIBRARY_PATH:-}"
fi

exec "$@"
