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

Optional, off by default: ``XBAND_BANDPASS_REL_TOL=<tol>`` in the environment
replaces ``isce3.splitspectrum.splitspectrum.check_range_bandwidth_overlap``
(used by ``bandpass_insar`` and by the writer's ``isMixedMode``) with a version
that treats centre frequencies and bandwidths within ``tol`` (relative) as
equal. The stock check uses ``!=``, so Capella pairs whose header values
differ by 26 Hz at 9.6 GHz (2.7e-9) and 0.54 Hz in bandwidth are bandpassed,
and the bandpass then fails its integer-ratio check on float rounding for
some pairs (``scripts/repro_bandpass_ratio_check.py``).
Finding candidate for the upstream RFC: the check should either use the
IEEE table or warn instead of raising.

Usage (inside the dev container)::

    python scripts/run_insar_xband.py runconfig.yaml [--restart]
"""

from __future__ import annotations

import os

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


def _make_overlap_check(rel_tol: float):
    """check_range_bandwidth_overlap with a relative tolerance instead of ``!=``."""
    import math

    from isce3.splitspectrum.splitspectrum import BandpassMetaData

    def check_range_bandwidth_overlap(ref_slc, sec_slc, pols):
        mode = {}
        for freq in pols:
            ref = BandpassMetaData.load_from_slc(ref_slc, freq)
            sec = BandpassMetaData.load_from_slc(sec_slc, freq)
            d_fc = abs(ref.center_freq - sec.center_freq)
            d_bw = abs(ref.rg_bandwidth - sec.rg_bandwidth)
            same = (math.isclose(ref.center_freq, sec.center_freq, rel_tol=rel_tol)
                    and math.isclose(ref.rg_bandwidth, sec.rg_bandwidth, rel_tol=rel_tol))
            if not same:
                mode[freq] = "ref" if ref.rg_bandwidth > sec.rg_bandwidth else "sec"
            print(f"run_insar_xband: overlap check freq {freq}: centre frequencies {d_fc:.3f} Hz apart, "
                  f"bandwidths {d_bw:.3f} Hz apart, rel_tol {rel_tol:g} -> "
                  f"{'bandpass ' + mode[freq] if freq in mode else 'no bandpass'}", flush=True)
        return mode

    return check_range_bandwidth_overlap


def main() -> None:
    _base.InSARBaseWriter._get_band_name = _get_band_name_ieee
    print("run_insar_xband: InSARBaseWriter._get_band_name patched to the IEEE band table")
    tol = os.environ.get("XBAND_BANDPASS_REL_TOL")
    if tol:
        from isce3.splitspectrum import splitspectrum as _ss
        _ss.check_range_bandwidth_overlap = _make_overlap_check(float(tol))
        print(f"run_insar_xband: check_range_bandwidth_overlap patched (rel_tol {float(tol):g})")

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
