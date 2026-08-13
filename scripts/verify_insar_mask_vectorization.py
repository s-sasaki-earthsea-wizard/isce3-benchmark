#!/usr/bin/env python3
"""Bitwise equivalence check for the vectorized generate_insar_mask.

Compares the vectorized ``nisar.products.insar.utils.generate_insar_mask``
(isce3 branch ``perf/vectorize-insar-mask``) against a verbatim frozen
copy of the pre-patch scalar per-pixel loop, on synthetic fixtures that
exercise every semantic edge the two implementations must agree on:

  * rounding asymmetry: sub-swath side uses ``int(x + 0.5)`` (truncation
    toward zero), exception-mask side uses Python ``round()``
    (round-half-even) — including exact ``k + 0.5`` values and negative
    offsets;
  * SubSwaths first-match-wins ordering, the empty-array-in-the-middle
    short-circuit, the no-sub-swath-information dataset (everything in
    bounds -> 1), and out-of-bounds indices -> 0;
  * rows/columns outside the reference swath -> whole-pixel 0;
  * secondary indices pushed outside the secondary swath -> exception
    bits dropped, sub-swath id 0 contribution kept;
  * exception-mask values with high bits (<< 16 / << 8 packing) and the
    dataset-absent path (zeros).

Run inside the isce3-benchmark container, with the patched isce3 source
tree overlaid so the pure-Python ``nisar`` package resolves from source
while the compiled ``isce3`` package stays from the install tree:

  docker compose run --rm -T dev bash -c \\
    'mkdir -p /tmp/ov && ln -sfn /opt/isce3-src/python/packages/nisar /tmp/ov/nisar && \\
     PYTHONPATH=/tmp/ov:$PYTHONPATH python3 scripts/verify_insar_mask_vectorization.py'

Exits 0 iff every case matches bitwise (uint32).
"""
import sys
import tempfile
import time

# pyre reads sys.argv eagerly during journal init (see run_crossmul.py)
_argv = sys.argv
sys.argv = sys.argv[:1]

import numpy as np
import h5py
from osgeo import gdal

import isce3
from nisar.products.insar import utils
from nisar.products.insar.utils import _compute_subswath_mask_id

sys.argv = _argv

SWATH_PATH = "/science/LSAR/RSLC/swaths"


# ---------------------------------------------------------------------------
# Frozen reference: verbatim copy of the pre-patch scalar loop body
# (isce3 develop 0a8df45dd, nisar/products/insar/utils.py).
# ---------------------------------------------------------------------------
def reference_generate_insar_mask(ref_rslc_obj,
                                  sec_rslc_obj,
                                  ref_rslc_h5_obj,
                                  sec_rslc_h5_obj,
                                  range_offset_path,
                                  azimuth_offset_path,
                                  freq,
                                  azi_idx_arr,
                                  rg_idx_arr):
    ref_swath = ref_rslc_obj.getSwathMetadata(freq)
    sec_swath = sec_rslc_obj.getSwathMetadata(freq)
    ref_subswaths = ref_rslc_obj.getSwathMetadata(freq).sub_swaths()
    sec_subswaths = sec_rslc_obj.getSwathMetadata(freq).sub_swaths()

    src_range_offset = gdal.Open(range_offset_path)
    src_azimuth_offset = gdal.Open(azimuth_offset_path)

    range_offset_band = src_range_offset.GetRasterBand(1)
    azimuth_offset_band = src_azimuth_offset.GetRasterBand(1)

    input_exception_mask_path = \
        lambda swath: f"{swath}/frequency{freq}/inputDataExceptionMask"

    def _load_exception_mask(h5_obj, rslc_obj, swath):
        path = input_exception_mask_path(rslc_obj.SwathPath)
        return h5_obj[path][()].astype(np.uint8) if path in h5_obj \
            else np.zeros((swath.lines, swath.samples), dtype=np.uint8)

    ref_input_exception_mask = _load_exception_mask(ref_rslc_h5_obj,
                                                    ref_rslc_obj,
                                                    ref_swath)
    sec_input_exception_mask = _load_exception_mask(sec_rslc_h5_obj,
                                                    sec_rslc_obj,
                                                    sec_swath)

    mask = []
    for i in azi_idx_arr:
        if i >= 0 and i < ref_swath.lines:
            range_off = \
                range_offset_band.ReadAsArray(0,
                                            int(i),
                                            ref_swath.samples,
                                            1)
            azimuth_off = \
                azimuth_offset_band.ReadAsArray(0,
                                                int(i),
                                                ref_swath.samples,
                                                1)
            for j in rg_idx_arr:
                mask_id = 0
                subswath_mask_id = 0
                ref_input_exception_mask_id = 0
                sec_input_exception_mask_id = 0

                if j >= 0 and j < ref_swath.samples:
                    subswath_mask_id = _compute_subswath_mask_id(int(i), int(j),
                                            azimuth_off[0, int(j)],
                                            range_off[0, int(j)],
                                            ref_subswaths,
                                            sec_subswaths)

                    ref_input_exception_mask_id = \
                        ref_input_exception_mask[int(i), int(j)] << 16

                    sec_i = round(i + azimuth_off[0, int(j)])
                    sec_j = round(j + range_off[0, int(j)])
                    if ((sec_i >= 0 and sec_i < sec_swath.lines) and
                            (sec_j >= 0 and sec_j < sec_swath.samples)):
                        sec_input_exception_mask_id = \
                            sec_input_exception_mask[sec_i, sec_j] << 8

                    mask_id = subswath_mask_id | ref_input_exception_mask_id \
                        | sec_input_exception_mask_id

                mask.append(mask_id)
        else:
            mask += [0] * len(rg_idx_arr)

    return np.array(mask).reshape(
        (len(azi_idx_arr),
         len(rg_idx_arr))).astype(np.uint32)


# ---------------------------------------------------------------------------
# Duck-typed stand-ins for the SLC / h5py objects generate_insar_mask uses
# ---------------------------------------------------------------------------
class FakeSwath:
    def __init__(self, lines, samples, subswaths):
        self.lines = lines
        self.samples = samples
        self._subswaths = subswaths

    def sub_swaths(self):
        return self._subswaths


class FakeSLC:
    SwathPath = SWATH_PATH

    def __init__(self, swath):
        self._swath = swath

    def getSwathMetadata(self, freq):
        return self._swath


def make_h5(freq, exception_mask):
    """In-memory h5py file; exception_mask=None omits the dataset."""
    f = h5py.File(f"fake-{id(exception_mask)}-{time.time_ns()}", "w",
                  driver="core", backing_store=False)
    if exception_mask is not None:
        f.create_dataset(
            f"{SWATH_PATH}/frequency{freq}/inputDataExceptionMask",
            data=exception_mask)
    return f


def make_offset_raster(path, data):
    drv = gdal.GetDriverByName("ENVI")
    ds = drv.Create(path, data.shape[1], data.shape[0], 1, gdal.GDT_Float64)
    ds.GetRasterBand(1).WriteArray(data)
    ds.FlushCache()
    ds = None


def make_subswaths(rng, lines, samples, n_sub, empty_at=(), no_vect=False):
    if no_vect:
        return isce3.product.SubSwaths(lines, samples, [])
    arrays = []
    for s in range(1, n_sub + 1):
        if s in empty_at:
            arrays.append(np.empty((0, 0), dtype=np.int32))
            continue
        start = rng.integers(0, samples, size=lines)
        width = rng.integers(0, samples // 2 + 1, size=lines)
        end = np.minimum(start + width, samples)
        # a few fully-invalid lines (start == end)
        invalid = rng.random(lines) < 0.1
        end[invalid] = start[invalid]
        arrays.append(
            np.stack([start, end], axis=1).astype(np.int32))
    return isce3.product.SubSwaths(lines, samples, arrays)


def build_offsets(rng, lines, samples, scale):
    """Offset fields with adversarial values: smooth random, exact
    k+0.5-hitting offsets, and large out-of-swath pushes."""
    off = rng.normal(0.0, scale, size=(lines, samples))
    # make (index + offset) land exactly on k + 0.5 for a subset
    jj = np.arange(samples, dtype=np.float64)
    half_rows = rng.choice(lines, size=max(1, lines // 5), replace=False)
    for r in half_rows:
        targets = rng.integers(-3, samples + 3, size=samples).astype(np.float64) + 0.5
        sel = rng.random(samples) < 0.3
        off[r, sel] = (targets - jj)[sel]
    # large pushes far outside the secondary swath
    blow = rng.random((lines, samples)) < 0.02
    off[blow] = rng.choice([-1.0, 1.0], size=blow.sum()) * (samples + lines)
    return off


def run_case(name, *, seed, ref_dims, sec_dims, n_sub, empty_at=(),
             no_vect=False, no_masks=False, off_scale=2.5, grid=None,
             tmpdir=None):
    rng = np.random.default_rng(seed)
    ref_lines, ref_samples = ref_dims
    sec_lines, sec_samples = sec_dims

    ref_ss = make_subswaths(rng, ref_lines, ref_samples, n_sub,
                            empty_at=empty_at, no_vect=no_vect)
    sec_ss = make_subswaths(rng, sec_lines, sec_samples, n_sub,
                            empty_at=(), no_vect=no_vect)

    ref_slc = FakeSLC(FakeSwath(ref_lines, ref_samples, ref_ss))
    sec_slc = FakeSLC(FakeSwath(sec_lines, sec_samples, sec_ss))

    if no_masks:
        ref_h5 = make_h5("A", None)
        sec_h5 = make_h5("A", None)
    else:
        ref_h5 = make_h5("A", rng.integers(0, 256, size=(ref_lines, ref_samples),
                                           dtype=np.uint8))
        sec_h5 = make_h5("A", rng.integers(0, 256, size=(sec_lines, sec_samples),
                                           dtype=np.uint8))

    rg_off_path = f"{tmpdir}/{name}_range.off"
    az_off_path = f"{tmpdir}/{name}_azimuth.off"
    make_offset_raster(rg_off_path, build_offsets(rng, ref_lines, ref_samples,
                                                  off_scale))
    make_offset_raster(az_off_path, build_offsets(rng, ref_lines, ref_samples,
                                                  off_scale))

    # integral-float index arrays, deliberately extending past the swath
    # on both sides (matches the np.round(...) arrays the callers build)
    n_az, n_rg = grid or (ref_lines + 8, ref_samples + 8)
    azi_idx = np.round(np.linspace(-3, ref_lines + 3, n_az))
    rg_idx = np.round(np.linspace(-3, ref_samples + 3, n_rg))

    args = (ref_slc, sec_slc, ref_h5, sec_h5,
            rg_off_path, az_off_path, "A", azi_idx, rg_idx)

    t0 = time.perf_counter()
    expected = reference_generate_insar_mask(*args)
    t_ref = time.perf_counter() - t0
    t0 = time.perf_counter()
    actual = utils.generate_insar_mask(*args)
    t_new = time.perf_counter() - t0

    ok = (actual.dtype == expected.dtype == np.uint32 and
          np.array_equal(actual, expected))
    n_px = expected.size
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {n_px} px, "
          f"ref {t_ref:.3f}s vs vectorized {t_new:.3f}s "
          f"(x{t_ref / max(t_new, 1e-9):.0f})")
    if not ok:
        bad = np.argwhere(actual != expected)
        for r, c in bad[:10]:
            print(f"    ({r},{c}) azi={azi_idx[r]} rg={rg_idx[c]}: "
                  f"expected {expected[r, c]:#010x} got {actual[r, c]:#010x}")
        print(f"    ... {len(bad)} mismatching pixels total")
    ref_h5.close()
    sec_h5.close()
    return ok


def main():
    print("utils module:", utils.__file__)
    ok = True
    with tempfile.TemporaryDirectory() as tmpdir:
        common = dict(tmpdir=tmpdir)
        ok &= run_case("random_3sub", seed=1, ref_dims=(60, 80),
                       sec_dims=(57, 83), n_sub=3, **common)
        ok &= run_case("random_1sub", seed=2, ref_dims=(45, 50),
                       sec_dims=(45, 50), n_sub=1, **common)
        ok &= run_case("empty_mid_subswath", seed=3, ref_dims=(40, 64),
                       sec_dims=(40, 64), n_sub=3, empty_at=(2,), **common)
        ok &= run_case("no_subswath_info", seed=4, ref_dims=(40, 64),
                       sec_dims=(38, 60), n_sub=0, no_vect=True, **common)
        ok &= run_case("no_exception_masks", seed=5, ref_dims=(40, 64),
                       sec_dims=(40, 64), n_sub=2, no_masks=True, **common)
        ok &= run_case("large_offsets", seed=6, ref_dims=(48, 56),
                       sec_dims=(30, 40), n_sub=2, off_scale=25.0, **common)
        ok &= run_case("perf_500x600", seed=7, ref_dims=(520, 640),
                       sec_dims=(516, 636), n_sub=3, grid=(500, 600),
                       **common)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
