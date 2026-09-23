#!/usr/bin/env python3
"""Run ``nisar.workflows.insar`` on a non-NISAR-band RSLC (bench#54 workaround).

isce3's InSAR product writer refuses anything that is not L- or S-band:
``InSARBaseWriter._get_band_name`` (python/packages/nisar/products/insar/
InSAR_base_writer.py) raises ``ValueError("Unknown frequency encountered.
Not L or S band")`` as soon as ``prepare_insar_hdf5`` builds the product
skeleton, although the band letter is only used for two metadata strings
(``identification/instrumentName`` = "<band>-SAR" and
``identification/radarBand``). Geometry, resampling and crossmul are band
agnostic -- ``rdr2geo`` / ``geo2rdr`` complete on the X-band Capella pair
before the writer stops the run (see the failed console logs of
2026-09-23).

This wrapper replaces that method with the IEEE band table (L, S, C, X,
Ku, K, Ka) and then runs the workflow exactly as ``python -m
nisar.workflows.insar`` would. It touches nothing in the isce3 source tree.
Finding candidate for the upstream RFC: the check should either use the
IEEE table or warn instead of raising.

Usage (inside the dev container)::

    python scripts/run_insar_xband.py runconfig.yaml [--restart]
"""

from __future__ import annotations

from nisar.products.insar import InSAR_base_writer as _base
from nisar.workflows import h5_prep
from nisar.workflows import insar as _insar
from nisar.workflows.insar_runconfig import InsarRunConfig
from nisar.workflows.persistence import Persistence
from nisar.workflows.yaml_argparse import YamlArgparse

IEEE_BANDS = [("L", 1.0, 2.0), ("S", 2.0, 4.0), ("C", 4.0, 8.0), ("X", 8.0, 12.0),
              ("Ku", 12.0, 18.0), ("K", 18.0, 27.0), ("Ka", 27.0, 40.0)]


def _get_band_name_ieee(self):
    """IEEE radar band letter from the reference RSLC's processed centre frequency."""
    freq = "A" if "A" in self.freq_pols else "B"
    path = f"{self.ref_rslc.SwathPath}/frequency{freq}/processedCenterFrequency"
    ghz = self.ref_h5py_file_obj[path][()] / 1e9
    for name, lo, hi in IEEE_BANDS:
        if lo <= ghz <= hi:
            return name
    raise ValueError(f"centre frequency {ghz:.3f} GHz is outside the IEEE L..Ka bands")


def main() -> None:
    _base.InSARBaseWriter._get_band_name = _get_band_name_ieee
    print("run_insar_xband: InSARBaseWriter._get_band_name patched to the IEEE band table")

    args = YamlArgparse().parse()
    runcfg = InsarRunConfig(args)
    logfile_path = runcfg.cfg["logging"]["path"]
    if logfile_path is None and runcfg.args.restart:
        raise ValueError("InSAR workflow persistence requires to specify a logfile")
    persist = Persistence(logfile_path, runcfg.args.restart)
    if persist.run:
        _, out_paths = h5_prep.get_products_and_paths(runcfg.cfg)
        _insar.run(runcfg.cfg, out_paths, persist.run_steps)


if __name__ == "__main__":
    main()
