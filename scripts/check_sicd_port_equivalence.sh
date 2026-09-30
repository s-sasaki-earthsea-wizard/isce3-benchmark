#!/usr/bin/env bash
# Equivalence of the isce3 example port vs the bench converter (runs in the container).
#
# The port is share/nisar/examples/sicd_to_nisar_rslc.py on the fork branch
# feat/sicd-rgzero-to-rslc (s-sasaki-earthsea-wizard/isce3), checked out as a
# worktree and mounted read-only at /opt/isce3-wt. For every Capella scene on
# disk and both radiometries, both converters write an RSLC, the two files are
# compared dataset by dataset (tools/compare_rslc_h5.py), and both are deleted.
# Only the provenance fields listed in EXP may differ.
#
# Usage (host):
#   docker compose run --rm -T -v <isce3 worktree>:/opt/isce3-wt:ro dev \
#       bash scripts/check_sicd_port_equivalence.sh
set -uo pipefail
O=/data/sicd_port_check; A=/work/artifacts/sicd-rgzero-port-20260930
EXP="science/LSAR/identification/processingDateTime science/LSAR/identification/processingCenter science/LSAR/identification/hasInputDataException@description science/LSAR/RSLC/metadata/processingInformation/inputs/converter science/LSAR/RSLC/metadata/processingInformation/inputs/conversionDiagnostics science/LSAR/RSLC/swaths/frequencyA/validSamplesSubSwath1@description"
SCENES="capella_mexico_city/CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055 capella_mexico_city/CAPELLA_C14_SM_SICD_HH_20240629134910_20240629134915 capella_reinforcement/CAPELLA_C13_SM_SICD_HH_20260204114511_20260204114516 capella_reinforcement/CAPELLA_C13_SM_SICD_HH_20260207104155_20260207104201 capella_reinforcement/CAPELLA_C13_SM_SICD_HH_20260204183253_20260204183257 capella_reinforcement/CAPELLA_C13_SM_SICD_HH_20260207172937_20260207172942 capella_reinforcement/CAPELLA_C15_SM_SICD_HH_20260627144157_20260627144201"
fail=0
for s in $SCENES; do
  id=$(basename $s); id=${id#*_HH_}; id=${id%%_*}
  for mode in dn beta0; do
    python tools/sicd_to_nisar_rslc.py /data/$s.ntf $O/bench.h5 --radiometry $mode --overwrite > $O/bench_${id}_$mode.log 2>&1 || { echo "FAIL bench $id $mode"; fail=1; continue; }
    python /opt/isce3-wt/share/nisar/examples/sicd_to_nisar_rslc.py /data/$s.ntf $O/port.h5 --radiometry $mode --overwrite > $A/port_${id}_$mode.log 2>&1 || { echo "FAIL port $id $mode"; fail=1; continue; }
    python tools/compare_rslc_h5.py $O/bench.h5 $O/port.h5 --expect $EXP --json $A/compare_${id}_$mode.json > $A/compare_${id}_$mode.txt 2>&1
    rc=$?; echo "COMPARE $id $mode rc=$rc $(tail -1 $A/compare_${id}_$mode.txt)"; [ $rc -eq 0 ] || fail=1
    rm -f $O/bench.h5 $O/port.h5
  done
done
echo "ALL DONE fail=$fail"
