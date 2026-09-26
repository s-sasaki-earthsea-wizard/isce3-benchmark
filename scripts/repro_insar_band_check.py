#!/usr/bin/env python3
"""isce3-only reproducer: the InSAR product writer rejects a non-L/S-band RSLC.

Uses only isce3's own test data and test runconfig (``tests/data/winnipeg.h5``,
``tests/data/insar_test.yaml``, the flow of
``tests/python/packages/nisar/workflows/insar.py``). The same RIFG run is made
three times, each time with one RSLC as both reference and secondary:

* control   -- ``winnipeg.h5`` unchanged (L-band, 1.243 GHz);
* xband     -- ``winnipeg.h5`` with processedCenterFrequency set to 9.65 GHz,
  nothing else changed;
* envisat   -- ``envisat.h5`` unchanged (C-band, 5.331 GHz), with the DEM of
  ``tests/data/geocodeslc/test_gslc.yaml``. This is the fixture the GSLC and
  GCOV workflow tests already process.

Steps after ``prepare_insar_hdf5`` are switched off, so the run takes seconds.
Expected on current develop: the control writes the RIFG skeleton; both other
runs stop in ``InSARBaseWriter._get_band_name`` with
``ValueError: Unknown frequency encountered. Not L or S band`` after rdr2geo and
geo2rdr have completed.

Usage (inside a container with isce3 + iscetest on the path)::

    PYTHONPATH=$PYTHONPATH:/opt/isce3-build/packages python scripts/repro_insar_band_check.py [--workdir DIR]

(``iscetest`` lives in the build tree's ``packages/``, not in the install tree;
append it -- prepending picks up the build tree's uninstalled ``isce3`` package.)
"""

import argparse
import os
import shutil
import sys
import tempfile
import traceback

_ARGV = sys.argv
sys.argv = [sys.argv[0]]
import h5py  # noqa: E402
import isce3  # noqa: E402
import iscetest  # noqa: E402
from nisar.workflows import insar  # noqa: E402
from nisar.workflows.h5_prep import get_products_and_paths  # noqa: E402
from nisar.workflows.insar_runconfig import InsarRunConfig  # noqa: E402
from nisar.workflows.persistence import Persistence  # noqa: E402
sys.argv = _ARGV

LATER_STEPS = ("coarse_resample", "dense_offsets", "offsets_product", "rubbersheet",
               "fine_resample", "crossmul", "filter_interferogram", "unwrap",
               "ionosphere", "geocode", "troposphere", "solid_earth_tides", "baseline")


def run_rifg(rslc: str, dem: str, workdir: str, tag: str) -> str:
    with open(os.path.join(iscetest.data, "insar_test.yaml")) as fh:
        text = (fh.read()
                .replace("@ISCETEST@/winnipeg.h5", rslc)
                .replace("@ISCETEST@/winnipeg_dem.tif", dem)
                .replace("@ISCETEST@", iscetest.data)
                .replace("@TEST_OUTPUT@", os.path.join(workdir, f"RIFG_{tag}.h5"))
                .replace("@TEST_PRODUCT_TYPES@", "RIFG")
                .replace("@TEST_RDR2GEO_FLAGS@", "False"))
    cwd = os.getcwd()
    os.chdir(workdir)
    try:
        cfg = InsarRunConfig(argparse.Namespace(run_config_path=text, log_file=False))
        cfg.geocode_common_arg_load()
        cfg.yaml_check()
        _, out_paths = get_products_and_paths(cfg.cfg)
        persist = Persistence(restart=True, logfile_path=f"insar_{tag}.log")
        for step in LATER_STEPS:
            if step in persist.run_steps:
                persist.run_steps[step] = False
        insar.run(cfg.cfg, out_paths, persist.run_steps)
        return "OK: RIFG skeleton written"
    except Exception as exc:  # noqa: BLE001 -- the failure is the result
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        return (f"{type(exc).__name__}: {exc}  "
                f"(raised in {os.path.basename(frame.filename)}:{frame.lineno} {frame.name})")
    finally:
        os.chdir(cwd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()
    work = args.workdir or tempfile.mkdtemp(prefix="band_check_")
    os.makedirs(work, exist_ok=True)
    print(f"isce3 {isce3.__version__}  test data {iscetest.data}  workdir {work}")

    winnipeg_dem = os.path.join(iscetest.data, "winnipeg_dem.tif")
    envisat_dem = os.path.join(iscetest.data, "geocode", "zeroHeightDEM.geo")
    cases = (("control", "winnipeg.h5", winnipeg_dem, None),
             ("xband", "winnipeg.h5", winnipeg_dem, 9.65e9),
             ("envisat", "envisat.h5", envisat_dem, None))
    results = {}
    for tag, src, dem, fc in cases:
        rslc = os.path.join(work, f"{tag}_{src}")
        shutil.copy(os.path.join(iscetest.data, src), rslc)
        with h5py.File(rslc, "r+") as f:
            swaths = [k for k in ("science/LSAR/RSLC/swaths", "science/LSAR/SLC/swaths") if k in f][0]
            for freq in ("frequencyA", "frequencyB"):
                ds = f"{swaths}/{freq}/processedCenterFrequency"
                if ds in f:
                    old = float(f[ds][()])
                    if fc is not None:
                        f[ds][()] = fc
                    print(f"{tag:8s} {src} {freq} processedCenterFrequency {old / 1e9:.4f} GHz"
                          f" -> {float(f[ds][()]) / 1e9:.4f} GHz")
        results[tag] = run_rifg(rslc, dem, work, tag)
    print()
    for tag, res in results.items():
        print(f"{tag:8s} {res}")
    ok = (results["control"].startswith("OK")
          and all("Not L or S band" in results[t] for t in ("xband", "envisat")))
    print("\nREPRODUCED" if ok else "\nNOT REPRODUCED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
