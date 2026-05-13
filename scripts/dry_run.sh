#!/usr/bin/env bash
# Dry-run: validate every benchmark runconfig WITHOUT running compute.
#
# Hard checks (fail the gate):
#   1. Workflow's own config loader parses the YAML (focus.load_config +
#      validate_config, or {GSLC,GCOV,Insar}RunConfig classes for the
#      orchestrator workflows).
#   2. All absolute file paths referenced under input_file_group and
#      dynamic_ancillary_file_group exist on the filesystem.
#
# Soft check (warn, do not fail):
#   3. yamale validation against share/nisar/schemas/<wf>.yaml. The schema
#      reflects strict PGE/PCM requirements; SAS-style minimal configs
#      legitimately omit several required-by-PGE fields and still run.
#
# Usage:
#   scripts/dry_run.sh                 # all configs/*_{cpu,gpu}.yaml
#   scripts/dry_run.sh path/to/x.yaml  # single file
set -euo pipefail
source "$(dirname "$0")/_common.sh"

ISCE3_INSTALL=${ISCE3_INSTALL:-/opt/isce3-build/install}
SCHEMAS_DIR="${ISCE3_INSTALL}/share/nisar/schemas"

if [ ! -d "${SCHEMAS_DIR}" ]; then
    echo "ERROR: schemas dir ${SCHEMAS_DIR} not found. Run \`make isce3\` first." >&2
    exit 1
fi

if [ "$#" -gt 0 ]; then
    cfgs=("$@")
else
    mapfile -t cfgs < <(ls "${BENCH_ROOT}"/configs/*_cpu.yaml "${BENCH_ROOT}"/configs/*_gpu.yaml 2>/dev/null)
fi

if [ "${#cfgs[@]}" -eq 0 ]; then
    echo "no configs to validate"; exit 0
fi

fail=0
for cfg in "${cfgs[@]}"; do
    if [ ! -f "${cfg}" ]; then
        echo "SKIP: ${cfg} not found"; continue
    fi

    wf="$(python -c "import yaml; print(yaml.safe_load(open('${cfg}'))['runconfig']['name'])")"
    schema="${SCHEMAS_DIR}/${wf}.yaml"

    echo "--- ${cfg}"
    echo "    workflow: ${wf}"

    # Skip secondary CSLC configs: they reference ${ref}/product, which only
    # exists after the corresponding ref CSLC run has completed. Validation
    # is implicit at runtime when run_bench.sh dispatches the sec config.
    if [[ "${cfg}" == *_sec_*.yaml || "${cfg}" == *_sec.yaml ]]; then
        echo "    [skip] secondary config (validated at runtime after ref completes)"
        continue
    fi

    # 1. Workflow-native config loader (HARD).
    if ! CFG="${cfg}" WF="${wf}" python <<'PY'
import os, sys, importlib, types
cfg_path = os.environ['CFG']; wf = os.environ['WF']

if wf == 'focus':
    mod = importlib.import_module('nisar.workflows.focus')
    mod.validate_config(mod.load_config(cfg_path))
elif wf == 'cslc_s1_workflow_default':
    # COMPASS S1 CSLC. Radar and geo modes use different RunConfig subclasses
    # and different defaults schemas; pick based on the filename convention.
    # `*_geo_*.yaml` → GeoRunConfig + s1_cslc_geo defaults.
    # everything else → RunConfig + s1_cslc (radar) defaults.
    if '_geo_' in os.path.basename(cfg_path) or os.path.basename(cfg_path).startswith('insar_s1_boso_geo'):
        rc_mod = importlib.import_module('compass.utils.geo_runconfig')
        cls = getattr(rc_mod, 'GeoRunConfig', None)
        if cls is not None:
            cls.load_from_yaml(cfg_path, workflow_name='s1_cslc_geo')
        else:
            print(f"    [warn] compass.utils.geo_runconfig has no GeoRunConfig; trusting runtime")
    else:
        rc_mod = importlib.import_module('compass.utils.runconfig')
        if hasattr(rc_mod, 'RunConfig'):
            rc_mod.RunConfig.load_from_yaml(cfg_path, workflow_name='s1_cslc')
        else:
            print(f"    [warn] compass.utils.runconfig has no RunConfig; trusting runtime")
elif wf == 'crossmul_s1':
    # Direct-primitive crossmul stage; no schema beyond presence of inputs.
    print(f"    [info] '{wf}' has no formal config loader; relying on path check")
else:
    runconfig_cls = {
        'gslc':  ('nisar.workflows.gslc_runconfig',  'GSLCRunConfig'),
        'gcov':  ('nisar.workflows.gcov_runconfig',  'GCOVRunConfig'),
        'insar': ('nisar.workflows.insar_runconfig', 'InsarRunConfig'),
    }.get(wf)
    if runconfig_cls is None:
        print(f"    [warn] no known config loader for workflow '{wf}'; skipping")
        sys.exit(0)
    mod_name, cls_name = runconfig_cls
    cls = getattr(importlib.import_module(mod_name), cls_name)
    args = types.SimpleNamespace(run_config_path=cfg_path, log_file=None)
    cls(args)

print("    [ok] workflow config loader accepted")
PY
    then
        echo "    [fail] workflow loader rejected the config"
        fail=1
        continue
    fi

    # 2. Input path existence (HARD).
    if ! CFG="${cfg}" python <<'PY'
import os, yaml, sys
d = yaml.safe_load(open(os.environ['CFG']))
groups = d['runconfig']['groups']
missing = []
def check(p):
    if isinstance(p, str) and p.startswith('/') and not os.path.exists(p):
        missing.append(p)
ifg = groups.get('input_file_group', {})
for f in ifg.get('input_file_path', []) or []:
    check(f)
# COMPASS uses safe_file_path + orbit_file_path lists
for f in ifg.get('safe_file_path', []) or []:
    check(f)
for f in ifg.get('orbit_file_path', []) or []:
    check(f)
for v in (groups.get('dynamic_ancillary_file_group') or {}).values():
    check(v)
if missing:
    print("    [fail] missing inputs:")
    for m in missing: print(f"        {m}")
    sys.exit(1)
print("    [ok] all referenced inputs exist")
PY
    then
        fail=1
        continue
    fi

    # 3. yamale schema (SOFT — informational). Only NISAR workflows have
    # schemas under ${ISCE3_INSTALL}/share/nisar/schemas/. COMPASS schemas
    # live under its own conda site-packages and are exercised by COMPASS's
    # own RunConfig.load_from_yaml in step 1, so we skip step 3 for those.
    if [ ! -f "${schema}" ]; then
        echo "    [info] no yamale schema at ${schema}; relying on workflow loader"
        continue
    fi
    SCHEMA="${schema}" CFG="${cfg}" python <<'PY' || true
import os, sys
import yamale
try:
    schema = yamale.make_schema(os.environ['SCHEMA'], parser='ruamel')
    data   = yamale.make_data(os.environ['CFG'],    parser='ruamel')
    yamale.validate(schema, data)
    print("    [ok] yamale schema strict-match (PGE-ready)")
except yamale.YamaleError as e:
    print("    [info] yamale schema mismatch (expected for SAS-minimal configs):")
    for r in e.results:
        for err in r.errors:
            print(f"        {err}")
PY
done

if [ "${fail}" -ne 0 ]; then
    echo "DRY-RUN FAILED"; exit 1
fi
echo "DRY-RUN OK"
