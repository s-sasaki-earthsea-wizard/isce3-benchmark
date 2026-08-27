#!/usr/bin/env python3
"""Implementation-agnostic oracle check for generate_insar_mask variants.

Validates whichever ``nisar.products.insar.utils.generate_insar_mask``
resolves on the current PYTHONPATH against a fully self-contained
scalar oracle, on the same synthetic case matrix as
``verify_insar_mask_vectorization.py``.

Unlike that script, the oracle here never imports any helper from the
module under test: the sub-swath lookup goes straight to the pybind
scalar API ``SubSwaths.get_sample_sub_swath`` and the exception-mask
bytes are packed with Python ints. This makes the check applicable to
any candidate rewrite of utils.py regardless of which private helpers
it keeps, renames, or re-signatures — e.g. upstream PR #358, whose
``_compute_subswath_mask_id`` no longer has the original scalar
signature, and PR #359, which keeps it.

The oracle encodes the pre-existing scalar semantics of isce3 develop
(utils.py blob 0643bde0c):

  * sub-swath lookup rounds with ``int(x + 0.5)`` (truncation toward
    zero), exception-mask lookup with Python ``round()`` (half-even);
  * SubSwaths first-match-wins ordering, empty-array short-circuit,
    the no-sub-swath-information dataset (in bounds -> sub-swath 1,
    per SubSwaths::getSampleSubSwath), out-of-bounds -> 0;
  * rows/columns outside the reference swath -> whole-pixel 0;
  * secondary indices outside the secondary swath -> secondary
    exception bits dropped, sub-swath digits kept;
  * ``(ref << 16) | (sec << 8)`` packing done in Python ints (the
    intended semantics, immune to NEP-50 fixed-width overflow).

Usage (inside the benchmark container):

  PYTHONPATH=/work/build/pr358_crosscheck/ov_<variant>:$PYTHONPATH \\
      python3 scripts/crosscheck_insar_mask_oracle.py [label]

Exits 0 iff every case matches the oracle bitwise (uint32).
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

sys.argv = _argv

LABEL = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"
SWATH_PATH = "/science/LSAR/RSLC/swaths"


# ---------------------------------------------------------------------------
# Self-contained scalar oracle (no imports from the module under test)
# ---------------------------------------------------------------------------
def oracle_generate_insar_mask(ref_slc, sec_slc, ref_h5, sec_h5,
                               range_offset_path, azimuth_offset_path,
                               freq, azi_idx_arr, rg_idx_arr):
    ref_swath = ref_slc.getSwathMetadata(freq)
    sec_swath = sec_slc.getSwathMetadata(freq)
    ref_subswaths = ref_swath.sub_swaths()
    sec_subswaths = sec_swath.sub_swaths()

    # Keep the Dataset objects referenced; a bare
    # gdal.Open(...).GetRasterBand(1) leaves the band dangling once the
    # Dataset is garbage-collected.
    src_range = gdal.Open(range_offset_path)
    src_azimuth = gdal.Open(azimuth_offset_path)
    range_band = src_range.GetRasterBand(1)
    azimuth_band = src_azimuth.GetRasterBand(1)

    def load_exception_mask(h5_obj, slc, swath):
        path = f"{slc.SwathPath}/frequency{freq}/inputDataExceptionMask"
        return h5_obj[path][()].astype(np.uint8) if path in h5_obj \
            else np.zeros((swath.lines, swath.samples), dtype=np.uint8)

    ref_exc = load_exception_mask(ref_h5, ref_slc, ref_swath)
    sec_exc = load_exception_mask(sec_h5, sec_slc, sec_swath)

    mask = np.zeros((len(azi_idx_arr), len(rg_idx_arr)), dtype=np.uint32)
    for row, i in enumerate(azi_idx_arr):
        if not (0 <= i < ref_swath.lines):
            continue
        range_off = range_band.ReadAsArray(
            0, int(i), ref_swath.samples, 1)[0]
        azimuth_off = azimuth_band.ReadAsArray(
            0, int(i), ref_swath.samples, 1)[0]
        for col, j in enumerate(rg_idx_arr):
            if not (0 <= j < ref_swath.samples):
                continue
            az = azimuth_off[int(j)]
            rg = range_off[int(j)]

            # Sub-swath digits: pybind scalar API, int(x + 0.5) rounding
            # exactly as the original _compute_subswath_mask_id did
            ref_num = ref_subswaths.get_sample_sub_swath(int(i), int(j))
            sec_num = sec_subswaths.get_sample_sub_swath(
                int(int(i) + az + 0.5), int(int(j) + rg + 0.5))
            mask_id = 10 * int(ref_num) + int(sec_num)

            # Exception-mask packing in Python ints (intended semantics)
            mask_id |= int(ref_exc[int(i), int(j)]) << 16
            sec_i = round(i + az)
            sec_j = round(j + rg)
            if (0 <= sec_i < sec_swath.lines and
                    0 <= sec_j < sec_swath.samples):
                mask_id |= int(sec_exc[sec_i, sec_j]) << 8

            mask[row, col] = mask_id
    return mask


# ---------------------------------------------------------------------------
# Duck-typed stand-ins (same fixtures as verify_insar_mask_vectorization)
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


def make_subswaths(rng, lines, samples, n_sub, empty_at=(), no_info=False):
    if no_info:
        return isce3.product.SubSwaths(lines, samples, [])
    arrays = []
    for s in range(1, n_sub + 1):
        if s in empty_at:
            arrays.append(np.empty((0, 0), dtype=np.int32))
            continue
        start = rng.integers(0, samples, size=lines)
        width = rng.integers(0, samples // 2 + 1, size=lines)
        end = np.minimum(start + width, samples)
        invalid = rng.random(lines) < 0.1
        end[invalid] = start[invalid]
        arrays.append(np.stack([start, end], axis=1).astype(np.int32))
    return isce3.product.SubSwaths(lines, samples, arrays)


def build_offsets(rng, lines, samples, scale):
    """Adversarial offsets: smooth random, exact k+0.5 landings where
    the two rounding rules diverge, large out-of-swath pushes."""
    off = rng.normal(0.0, scale, size=(lines, samples))
    jj = np.arange(samples, dtype=np.float64)
    half_rows = rng.choice(lines, size=max(1, lines // 5), replace=False)
    for r in half_rows:
        targets = rng.integers(-3, samples + 3,
                               size=samples).astype(np.float64) + 0.5
        sel = rng.random(samples) < 0.3
        off[r, sel] = (targets - jj)[sel]
    blow = rng.random((lines, samples)) < 0.02
    off[blow] = rng.choice([-1.0, 1.0], size=blow.sum()) * (samples + lines)
    return off


def run_case(name, *, seed, ref_dims, sec_dims, n_sub, empty_at=(),
             no_info=False, no_masks=False, off_scale=2.5, tmpdir=None):
    rng = np.random.default_rng(seed)
    ref_lines, ref_samples = ref_dims
    sec_lines, sec_samples = sec_dims

    ref_slc = FakeSLC(FakeSwath(
        ref_lines, ref_samples,
        make_subswaths(rng, ref_lines, ref_samples, n_sub,
                       empty_at=empty_at, no_info=no_info)))
    sec_slc = FakeSLC(FakeSwath(
        sec_lines, sec_samples,
        make_subswaths(rng, sec_lines, sec_samples, n_sub,
                       no_info=no_info)))

    if no_masks:
        ref_h5 = make_h5("A", None)
        sec_h5 = make_h5("A", None)
    else:
        ref_h5 = make_h5("A", rng.integers(
            0, 256, size=(ref_lines, ref_samples), dtype=np.uint8))
        sec_h5 = make_h5("A", rng.integers(
            0, 256, size=(sec_lines, sec_samples), dtype=np.uint8))

    rg_off_path = f"{tmpdir}/{name}_range.off"
    az_off_path = f"{tmpdir}/{name}_azimuth.off"
    make_offset_raster(rg_off_path,
                       build_offsets(rng, ref_lines, ref_samples, off_scale))
    make_offset_raster(az_off_path,
                       build_offsets(rng, ref_lines, ref_samples, off_scale))

    # integral-float index arrays extending past the swath on both sides
    azi_idx = np.round(np.linspace(-3, ref_lines + 3, ref_lines + 8))
    rg_idx = np.round(np.linspace(-3, ref_samples + 3, ref_samples + 8))

    args = (ref_slc, sec_slc, ref_h5, sec_h5,
            rg_off_path, az_off_path, "A", azi_idx, rg_idx)

    expected = oracle_generate_insar_mask(*args)
    t0 = time.perf_counter()
    actual = utils.generate_insar_mask(*args)
    t_new = time.perf_counter() - t0

    ok = (actual.dtype == np.uint32 and np.array_equal(actual, expected))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {expected.size} px, "
          f"candidate {t_new:.3f}s")
    if not ok:
        bad = np.argwhere(actual != expected)
        for r, c in bad[:8]:
            print(f"    ({r},{c}) azi={azi_idx[r]} rg={rg_idx[c]}: "
                  f"oracle {expected[r, c]:#010x} got {actual[r, c]:#010x}")
        print(f"    ... {len(bad)} mismatching pixels total "
              f"({len(bad) / expected.size:.1%})")
    ref_h5.close()
    sec_h5.close()
    return ok


def main():
    print(f"variant: {LABEL}")
    print(f"utils module: {utils.__file__}")
    print(f"numpy {np.__version__}, isce3 {isce3.__version__}")
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
                       sec_dims=(38, 60), n_sub=0, no_info=True, **common)
        ok &= run_case("no_exception_masks", seed=5, ref_dims=(40, 64),
                       sec_dims=(40, 64), n_sub=2, no_masks=True, **common)
        ok &= run_case("large_offsets", seed=6, ref_dims=(48, 56),
                       sec_dims=(30, 40), n_sub=2, off_scale=25.0, **common)
    print(f"RESULT[{LABEL}]:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
