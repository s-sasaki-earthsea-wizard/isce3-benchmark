#!/usr/bin/env bash
# Tier 0 runtime A/B for the GDAL ENVI multiband-BSQ regression (issue #19).
#
# Runs the native gdal_create repro (no Python bindings involved) against:
#   - GDAL 3.12.1  (disposable micromamba env; expected: SUCCESS)
#   - GDAL 3.12.2  (disposable micromamba env; expected: "Int overflow occurred.")
#   - GDAL 3.12.3  (dev-container base env;    expected: "Int overflow occurred.")
# plus the -ot Byte pitfall check on 3.12.3 (expected: SUCCESS, band stride
# stays under INT32_MAX with 1-byte samples).
#
# Dimensions are the official NISAR frequency-A GCOV grid (34992 x 34416,
# 2 bands, Float32 -> band stride 4.82 GB > INT32_MAX).
#
# Intended to run inside the dev container:
#   docker compose run --rm dev bash scripts/repro_gdal_envi_ab.sh
#
# Disposable envs are created under /tmp/gdal_ab (container-local, discarded
# with the container). The blessed benchmark env is not modified.
set -u

ENV_ROOT=/tmp/gdal_ab
OUT_ROOT=/tmp/gdal_ab_out
DIMS="-outsize 34992 34416 -bands 2"
mkdir -p "$ENV_ROOT" "$OUT_ROOT"

echo "=== provenance ==="
date -u +"timestamp_utc: %Y-%m-%dT%H:%M:%SZ"
echo "host: $(hostname)"
micromamba --version | sed 's/^/micromamba: /'
echo "base env GDAL: $(gdalinfo --version)"
echo

make_env() {
    local ver="$1" prefix="$ENV_ROOT/$2"
    echo "--- solving disposable env: libgdal-core=$ver -> $prefix"
    micromamba create -y -q -p "$prefix" -c conda-forge --override-channels \
        "libgdal-core=$ver" >/dev/null
    micromamba list -p "$prefix" | grep -E 'libgdal-core|^ *proj ' \
        | sed 's/^/    /'
}

run_repro() {
    local label="$1" prefix="$2" dtype="$3"
    local out="$OUT_ROOT/${label}_${dtype}.bin"
    local cmd=(gdal_create -of ENVI -ot "$dtype" $DIMS "$out")
    echo "=== [$label] ${cmd[*]} ==="
    if [ -n "$prefix" ]; then
        micromamba run -p "$prefix" gdalinfo --version | sed 's/^/version: /'
        micromamba run -p "$prefix" "${cmd[@]}" 2>&1
    else
        gdalinfo --version | sed 's/^/version: /'
        "${cmd[@]}" 2>&1
    fi
    local rc=$?
    echo "exit code: $rc"
    # ls -ls shows apparent size vs allocated blocks (raw ENVI files are sparse)
    ls -ls "$out" "${out%.bin}"*.hdr 2>/dev/null | sed 's/^/created: /'
    rm -f "$out" "${out%.bin}"*.hdr "${out%.bin}"*.aux.xml
    echo
}

make_env 3.12.1 gdal3121
make_env 3.12.2 gdal3122
echo

run_repro "3.12.1" "$ENV_ROOT/gdal3121" Float32
run_repro "3.12.2" "$ENV_ROOT/gdal3122" Float32
run_repro "3.12.3-base" "" Float32
run_repro "3.12.3-base-byte-pitfall" "" Byte

echo "=== done ==="
