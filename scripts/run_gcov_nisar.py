#!/usr/bin/env python3
"""Launcher for the NISAR GCOV workflow with a bench-side workaround.

Why this exists (instead of ``python -m nisar.workflows.gcov``):

When the runconfig selects ``output_gcov_terms.format: GTiff`` — required at
NISAR frequency-A nominal scale, because the default HDF5 setting forces an
ENVI scratch raster whose per-band size (e.g. 29240 x 21232 x 4 B = 2.48 GB)
exceeds INT32_MAX and trips a GDAL 3.12 ENVI-driver "Int overflow" (which
isce3.io.Raster then turns into a segfault by not checking the null dataset) —
the GTiff writer path crashes at the very end of the workflow:

    BaseL2WriterSingleInput.save_raster()
        raster_out.SetDescription(long_name)   # long_name is None
    TypeError: Received a NULL pointer.

``save_dataset()`` forwards ``long_name=None`` for the GCOV imagery terms
(GcovWriter.run_geocode_cov passes only ``**output_gcov_terms_kwargs``, which
come straight from the runconfig ``output`` group and cannot carry
``long_name``). This launcher wraps ``save_raster`` to substitute an empty
string, which GDAL accepts.

Upstream status: recorded as a finding in the isce3-benchmark project;
not yet filed upstream (single-upstream-footprint policy until #338 settles).

Usage:
    python scripts/run_gcov_nisar.py <runconfig.yaml>
"""

import sys

# pyre's journal reads sys.argv eagerly at import time; keep only argv[0]
# during isce3/nisar imports (same gotcha as scripts/run_crossmul.py).
_argv = sys.argv[:]
sys.argv = [_argv[0]]

# NOTE: plain "import nisar.products.writers.BaseL2WriterSingleInput" would
# bind the CLASS of the same name (re-exported by the package __init__), not
# the module — go through importlib to get the module object.
import importlib  # noqa: E402

_base_writer = importlib.import_module(
    "nisar.products.writers.BaseL2WriterSingleInput")
from nisar.workflows import gcov  # noqa: E402
from nisar.workflows.gcov_runconfig import GCOVRunConfig  # noqa: E402
from nisar.workflows.yaml_argparse import YamlArgparse  # noqa: E402

_orig_save_raster = _base_writer.save_raster


def _save_raster_gdal312_fix(*args, **kwargs):
    """Work around two GDAL 3.12 incompatibilities in save_raster:

    1. ``raster_out.SetDescription(long_name)`` with ``long_name=None``
       raises ``TypeError: Received a NULL pointer.`` -> substitute ''.
    2. ``band_out.SetNoDataValue(fill_value)`` with a ``np.float32`` scalar
       raises ``TypeError: ... argument 2 of type 'double'`` (GDAL 3.12
       SWIG no longer auto-converts numpy scalars) -> coerce to float.
    """
    if kwargs.get("long_name") is None:
        kwargs["long_name"] = kwargs.get("standard_name") or ""
    if kwargs.get("fill_value") is not None:
        kwargs["fill_value"] = float(kwargs["fill_value"])
    return _orig_save_raster(*args, **kwargs)


_base_writer.save_raster = _save_raster_gdal312_fix


def main():
    sys.argv = _argv
    yaml_parser = YamlArgparse()
    args = yaml_parser.parse()
    runconfig = GCOVRunConfig(args)
    gcov.run(runconfig.cfg)


if __name__ == "__main__":
    main()
