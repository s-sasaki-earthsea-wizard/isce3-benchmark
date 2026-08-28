#!/bin/bash
# Runs INSIDE the dev container: one standalone dense_offsets replicate for
# the FFTW planner A/B (bench#48). The arm under test is whatever binary is
# installed in the mounted build tree; planner observation and (for arm B)
# wisdom pinning are controlled by PYCUAMPCOR_FFTW_* env passed from the
# host runner.
set -uo pipefail

echo "rep=${REP_ID:?} run_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset} MKL_NUM_THREADS=${MKL_NUM_THREADS:-unset}"
echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-unset}"
echo "PYCUAMPCOR_FFTW_LOG=${PYCUAMPCOR_FFTW_LOG:-unset}"
echo "PYCUAMPCOR_FFTW_WISDOM_EXPORT=${PYCUAMPCOR_FFTW_WISDOM_EXPORT:-unset}"
echo "PYCUAMPCOR_FFTW_WISDOM_IMPORT=${PYCUAMPCOR_FFTW_WISDOM_IMPORT:-unset}"
echo "nproc=$(nproc)"
python3 -c "import isce3, sys; print('isce3', isce3.__version__)"

/usr/bin/time -v python3 -m nisar.workflows.dense_offsets /ab/configs/dof_rep.yaml
rc=$?
echo "dense_offsets exit rc=$rc"
exit $rc
