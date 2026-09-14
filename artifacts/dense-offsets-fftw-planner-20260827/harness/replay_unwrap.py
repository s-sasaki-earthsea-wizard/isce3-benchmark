#!/usr/bin/env python3
"""Standalone unwrap-step replay driver (bench#36 Step 2).

Replicates nisar.workflows.unwrap __main__ but bypasses
prepare_insar_hdf5.run(): the RUNW_STANDALONE product type is missing from
h5_prep.get_products_and_paths()'s product_dict (KeyError on isce3
v0.25.16 and current develop), so we call prepare_insar_hdf5() directly
for the RUNW product only.

Inputs come from a seeded scratch (phase0 run):
  - /seed/RIFG.h5                      (crossmul_path)
  - /scratch/geo2rdr                   (symlink, ro)
  - /scratch/fine_resample_slc         (symlink, ro)
  - /scratch/rdr2geo/freqA/{x,y,z}.rdr (file symlinks, ro)
Outputs (per replicate):
  - /out/RUNW.h5 (sas_output_file)
  - /scratch/crossmul, /scratch/unwrap (fresh, retained)
"""
import pathlib
import time

from nisar.workflows import unwrap
from nisar.workflows.prepare_insar_hdf5 import prepare_insar_hdf5
from nisar.workflows.unwrap_runconfig import UnwrapRunConfig
from nisar.workflows.yaml_argparse import YamlArgparse

if __name__ == "__main__":
    args = YamlArgparse().parse()
    rc = UnwrapRunConfig(args)

    runw_path = rc.cfg["product_path_group"]["sas_output_file"]
    rifg_path = rc.cfg["processing"]["phase_unwrap"]["crossmul_path"]

    t0 = time.time()
    prepare_insar_hdf5(rc.cfg, runw_path, "RUNW")
    print(f"replay: prepared RUNW skeleton in {time.time() - t0:.3f} s",
          flush=True)

    # unwrap.run() re-invokes crossmul at the unwrap looks (13x16 here), and
    # crossmul.run() opens `<scratch>/crossmul/product.h5` with h5py mode 'a'
    # BEFORE the loop body that mkdir()s `<scratch>/crossmul/freq<F>`. On a
    # fresh scratch the parent directory does not exist yet and the open fails
    # with FileNotFoundError. Inside insar.py this is masked because the main
    # crossmul step already created the directory. Create it here; this is a
    # harness workaround, not an isce3 source change.
    scratch = pathlib.Path(rc.cfg["product_path_group"]["scratch_path"])
    (scratch / "crossmul").mkdir(parents=True, exist_ok=True)

    t1 = time.time()
    unwrap.run(rc.cfg, rifg_path, runw_path)
    print(f"replay: unwrap.run finished in {time.time() - t1:.3f} s",
          flush=True)
