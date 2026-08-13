#!/usr/bin/env python3
"""Synthetic-data timing reproducer for the generate_insar_mask loop.

Requires only isce3 (with the nisar package), NumPy, GDAL and h5py — no
NISAR granules. Builds a synthetic SubSwaths layout and synthetic
geometric-coregistration offset rasters at a production-like grid size,
then times a single ``generate_insar_mask()`` call. Run it against a
pristine isce3 install to see the per-pixel-loop cost, and against the
``perf/vectorize-insar-mask`` branch to see the vectorized cost; the
printed ``mask crc`` is identical when the outputs match.

Usage:
    python3 repro_insar_mask_timing.py [lines] [samples]

Defaults to 6840 x 10581 — the RIFG interferogram grid of a NISAR
L-band frame (72.4 Mpx). Pass smaller dims for a quicker look.
"""
import sys
import tempfile
import time
import zlib

# pyre's journal reads sys.argv eagerly on import; stash it
_argv = sys.argv
sys.argv = sys.argv[:1]

import numpy as np
import h5py
from osgeo import gdal

import isce3
from nisar.products.insar.utils import generate_insar_mask

sys.argv = _argv

lines = int(sys.argv[1]) if len(sys.argv) > 1 else 6840
samples = int(sys.argv[2]) if len(sys.argv) > 2 else 10581

rng = np.random.default_rng(0)

# Three sub-swaths with jittered per-line [start, end) valid-sample
# intervals spanning the swath
edges = np.linspace(0, samples, 4).astype(np.int64)
arrays = []
for s in range(3):
    start = edges[s] + rng.integers(-20, 20, lines)
    end = edges[s + 1] + rng.integers(-20, 20, lines)
    arrays.append(np.stack([np.clip(start, 0, samples),
                            np.clip(end, 0, samples)],
                           axis=1).astype(np.int32))
subswaths = isce3.product.SubSwaths(lines, samples, arrays)


class Swath:
    def __init__(self):
        self.lines, self.samples = lines, samples

    def sub_swaths(self):
        return subswaths


class SLC:
    SwathPath = "/science/LSAR/RSLC/swaths"

    def getSwathMetadata(self, freq):
        return Swath()


tmpdir = tempfile.mkdtemp()
paths = []
drv = gdal.GetDriverByName("ENVI")
for name in ("range.off", "azimuth.off"):
    path = f"{tmpdir}/{name}"
    ds = drv.Create(path, samples, lines, 1, gdal.GDT_Float64)
    ds.GetRasterBand(1).WriteArray(rng.normal(0.0, 3.0, (lines, samples)))
    ds.FlushCache()
    ds = None
    paths.append(path)

# Empty in-memory HDF5: the inputDataExceptionMask datasets are absent,
# which exercises the zeros fallback (the per-pixel packing still runs)
h5 = h5py.File("repro", "w", driver="core", backing_store=False)

azi_idx = np.round(np.arange(lines, dtype=np.float64))
rg_idx = np.round(np.arange(samples, dtype=np.float64))

t0 = time.perf_counter()
mask = generate_insar_mask(SLC(), SLC(), h5, h5,
                           paths[0], paths[1], "A", azi_idx, rg_idx)
dt = time.perf_counter() - t0

n_px = lines * samples
print(f"generate_insar_mask: {lines}x{samples} = {n_px / 1e6:.1f} Mpx "
      f"in {dt:.1f} s ({dt / n_px * 1e6:.2f} us/px), "
      f"mask crc = {zlib.crc32(np.ascontiguousarray(mask)):#010x}")
