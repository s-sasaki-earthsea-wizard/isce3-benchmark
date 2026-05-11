#!/usr/bin/env python3
"""
ISCE3-only minimal repro of the CUDA Rdr2Geo.topo() optional-raster kwarg
regression.

This script imports nothing beyond isce3 and its own test fixtures
(envisat.h5, srtm_cropped.tif from tests/data/). No COMPASS, no
Sentinel-1, no nisar.workflows. It surfaces the binding gap as a pure
API-parity issue between isce3.geometry.Rdr2Geo (CPU sibling) and
isce3.cuda.geometry.Rdr2Geo (CUDA sibling) of the same C++ class
isce3::cuda::geometry::Topo::topo(), whose signature accepts
`isce3::io::Raster*` (i.e. nullptr) for every output raster argument.

Two-layer evidence:

  Layer 1  Inspect the docstring signature emitted by pybind11 for the
           second `topo()` overload on each sibling. Count how many of
           the 11 output raster kwargs carry `= None` (the pybind11
           docstring rendering of `py::arg("name") = nullptr`).

  Layer 2  Call topo() with a kwarg subset (x/y/height only, the other
           8 layers omitted entirely) on each sibling. The C++ class
           contract accepts this call shape. Report PASS / TypeError.

Expected output against upstream isce-framework/isce3 develop:

    Layer 1: CPU=11 defaults, CUDA=0 defaults  <- the regression
    Layer 2: CPU PASS, CUDA TypeError

Expected output against the proposed fix branch
feat/cuda-rdr2geo-optional-kwargs of the same fork:

    Layer 1: CPU=11 defaults, CUDA=11 defaults
    Layer 2: CPU PASS, CUDA PASS
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap


def _find_topo_multilayer_overload(doc: str) -> str | None:
    """
    Return the docstring text for the multi-raster `topo()` overload
    (overload #2 in pybind11's listing). Returns None if not found.
    """
    if doc is None:
        return None
    # pybind11 numbers overloads `1.`, `2.`, ... in the docstring. The
    # multi-raster topo is overload 2. Match from `2. topo(` up to the
    # next `\n` that is not a continuation (we accept multiline kwarg
    # lists; pybind11 emits them on one logical line so a single
    # regex is sufficient).
    m = re.search(r"^\s*2\.\s+topo\([^)]*\)\s*->\s*\w+", doc, flags=re.MULTILINE)
    return m.group(0) if m else None


def _count_defaults(sig: str) -> int:
    return sig.count("= None")


def layer_1_signature_inspection() -> dict[str, int | None]:
    """
    Print the per-sibling default count and a wrapped form of the
    second-overload signature. Return a dict of counts for the
    caller's summary.
    """
    import isce3

    print("=" * 70)
    print("Layer 1: pybind11 docstring signature inspection")
    print("=" * 70)

    siblings: list[tuple[str, object]] = [
        ("CPU  isce3.geometry.Rdr2Geo", isce3.geometry.Rdr2Geo),
    ]
    if hasattr(isce3, "cuda"):
        siblings.append(
            ("CUDA isce3.cuda.geometry.Rdr2Geo", isce3.cuda.geometry.Rdr2Geo)
        )
    else:
        print("  (isce3 built without CUDA; CUDA sibling unavailable)\n")

    counts: dict[str, int | None] = {}
    for label, cls in siblings:
        sig = _find_topo_multilayer_overload(cls.topo.__doc__)
        if sig is None:
            print(f"-- {label}.topo: could not locate overload #2 signature\n")
            counts[label] = None
            continue
        n_defaults = _count_defaults(sig)
        counts[label] = n_defaults
        print(f"-- {label}.topo --")
        print(f"   kwargs with `= None` default: {n_defaults}")
        print(textwrap.indent(textwrap.fill(sig, 100), "   "))
        print()

    return counts


def layer_2_runtime_kwarg_subset(test_data_dir: str) -> dict[str, str]:
    """
    Instantiate Rdr2Geo from each sibling and call topo() with x/y/z
    requested, the other 8 layers omitted. Returns a dict label ->
    "PASS" | error class name.
    """
    import tempfile

    import isce3
    from osgeo import gdal
    from nisar.products.readers import SLC

    print("=" * 70)
    print("Layer 2: runtime call with kwarg subset (x/y/height only)")
    print("=" * 70)

    h5_path = os.path.join(test_data_dir, "envisat.h5")
    dem_path = os.path.join(test_data_dir, "srtm_cropped.tif")
    for path in (h5_path, dem_path):
        if not os.path.exists(path):
            sys.exit(f"required isce3 test fixture missing: {path}")

    radargrid = isce3.product.RadarGridParameters(h5_path)
    slc = SLC(hdf5file=h5_path)
    orbit = slc.getOrbit()
    doppler = slc.getDopplerCentroid()
    ellipsoid = isce3.core.Ellipsoid()

    dem_raster = isce3.io.Raster(dem_path)
    length, width = radargrid.shape

    siblings: list[tuple[str, object]] = [
        ("CPU  isce3.geometry.Rdr2Geo", isce3.geometry.Rdr2Geo),
    ]
    if hasattr(isce3, "cuda"):
        siblings.append(
            ("CUDA isce3.cuda.geometry.Rdr2Geo", isce3.cuda.geometry.Rdr2Geo)
        )

    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="repro_rdr2geo_") as scratch:
        for label, Rdr2Geo in siblings:
            obj = Rdr2Geo(radargrid, orbit, ellipsoid, doppler, threshold=1e-7)
            prefix = os.path.join(scratch, label.split()[0].lower())
            x_raster, y_raster, height_raster = (
                isce3.io.Raster(f"{prefix}_{n}.rdr", width, length, 1,
                                gdal.GDT_Float64, "ENVI")
                for n in ("x", "y", "z")
            )
            try:
                obj.topo(
                    dem_raster,
                    x_raster=x_raster,
                    y_raster=y_raster,
                    height_raster=height_raster,
                )
                print(f"-- {label}.topo(dem, x=..., y=..., z=...): PASS\n")
                results[label] = "PASS"
            except TypeError as e:
                cls_name = type(e).__name__
                print(f"-- {label}.topo(dem, x=..., y=..., z=...): "
                      f"FAIL with {cls_name}")
                print(textwrap.indent(str(e), "   "))
                print()
                results[label] = cls_name

    return results


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--isce3-test-data",
        default=os.environ.get("ISCE3_TEST_DATA"),
        help="Path to isce3/tests/data/. Defaults to $ISCE3_TEST_DATA "
             "or `iscetest.data` from the installed isce3 tests "
             "package.",
    )
    p.add_argument(
        "--no-layer-2", action="store_true",
        help="Skip the runtime call (Layer 1 inspection only).",
    )
    args = p.parse_args()

    import isce3
    print(f"isce3.__file__: {isce3.__file__}")
    print(f"CUDA built:     {hasattr(isce3, 'cuda')}")
    print()

    counts = layer_1_signature_inspection()

    if args.no_layer_2:
        return 0

    test_data_dir = args.isce3_test_data
    if test_data_dir is None:
        try:
            import iscetest
            test_data_dir = iscetest.data
        except ImportError:
            sys.exit("test data dir not found; pass --isce3-test-data, set "
                     "ISCE3_TEST_DATA, or install isce3 tests so that "
                     "`import iscetest` works.")

    results = layer_2_runtime_kwarg_subset(test_data_dir)

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    for label, count in counts.items():
        print(f"  {label}: defaults={count}, runtime={results.get(label, 'N/A')}")
    print()
    print("Interpretation:")
    print("  If CPU and CUDA both show defaults=11 and runtime=PASS, the")
    print("  binding regression is fixed in this build.")
    print("  If CPU shows defaults=11/PASS but CUDA shows defaults=0/TypeError,")
    print("  the binding regression is live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
