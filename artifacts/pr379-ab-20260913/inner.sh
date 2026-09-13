#!/bin/bash
# Inner runner for the PR #379 A/B: standalone seeded-scratch prepare_insar_hdf5.
# stdout -> journal log, stderr -> run log (module/md5 echoes + /usr/bin/time -v).
set -uo pipefail
export PYTHONPATH=/tmp/ov${PYTHONPATH:+:$PYTHONPATH}
{
  echo "nisar_module: $(python3 -c 'import nisar; print(nisar.__file__)')"
  for f in products/insar/utils.py products/insar/GUNW_writer.py \
           products/insar/GOFF_writer.py products/insar/ROFF_writer.py \
           products/insar/InSAR_L1_writer.py workflows/geocode_insar.py \
           workflows/geocode_insar_runconfig.py; do
    echo "md5 $f $(md5sum /tmp/ov/nisar/$f | cut -d' ' -f1)"
  done
} 1>&2
exec /usr/bin/time -v python3 -m nisar.workflows.prepare_insar_hdf5 \
  /configs/insar_gunw_ASC139_019_20260705_20260717_gpu.yaml
