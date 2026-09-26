#!/usr/bin/env bash
# Stock InSAR CLI on isce3's own C-band test RSLC (tests/data/envisat.h5).
#
# Takes tests/data/insar_test.yaml, points it at envisat.h5 and the DEM of
# tests/data/geocodeslc/test_gslc.yaml (the GSLC and GCOV workflow tests
# already process envisat.h5), adds the logging group the CLI entry point
# requires, and runs `python -m nisar.workflows.insar`. On current develop
# rdr2geo and geo2rdr complete, then prepare_insar_hdf5 stops in
# InSARBaseWriter._get_band_name with
#   ValueError: Unknown frequency encountered. Not L or S band
#
# Usage (inside the dev container):
#   bash scripts/repro_insar_band_check_envisat.sh [ISCE3_TESTS_DATA_DIR] [WORKDIR]
set -euo pipefail
D=${1:-/opt/isce3-src/tests/data}
W=${2:-/tmp/envisat_insar}
mkdir -p "$W" && cd "$W"
sed -e "s|@ISCETEST@/winnipeg.h5|$D/envisat.h5|" \
    -e "s|@ISCETEST@/winnipeg_dem.tif|$D/geocode/zeroHeightDEM.geo|" \
    -e "s|@ISCETEST@|$D|" \
    -e "s|@TEST_OUTPUT@|RIFG_envisat.h5|" \
    -e "s|@TEST_PRODUCT_TYPES@|RIFG|" \
    -e "s|@TEST_RDR2GEO_FLAGS@|False|" \
    -e "s|^    groups:|    groups:\n        logging:\n            path: insar_envisat.log|" \
    "$D/insar_test.yaml" > insar_envisat.yaml
python -m nisar.workflows.insar insar_envisat.yaml
