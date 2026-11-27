"""Tests for the multi-frequency crop of tools/crop_rslc.py.

Runs inside the dev container (the tool imports isce3). The products are
small synthetic HDF5 files with the NISAR RSLC swath layout: one shared
``zeroDopplerTime`` and, for the dual-band case, a frequency B that starts at
frequency A's first slant range with 8x its spacing, as in a 40+5 MHz frame.
window_for (geo2rdr) needs a real orbit and is not exercised here; the tests
start from the line/sample window it returns.
"""

import h5py
import numpy as np
import pytest

crop_rslc = pytest.importorskip("crop_rslc")

from crop_rslc import SWATHS, align_lines, crop_copy, frequency_windows  # noqa: E402

N_LINES = 40
R0 = 884337.78569
DR_A = 3.1228381
RATIO = 8
W_A = 203          # not a multiple of 8, like the 52943 samples of A
W_B = 26           # 26 * 8 = 208 > 203: B reaches a little past A, as in the frame
LUT = "science/LSAR/RSLC/metadata/calibrationInformation/frequencyA/HH/elevationAntennaPattern"


def _band(g, width, dr, r0, seed):
    rng = np.random.default_rng(seed)
    img = (rng.standard_normal((N_LINES, width))
           + 1j * rng.standard_normal((N_LINES, width))).astype(np.complex64)
    g.create_dataset("HH", data=img, chunks=(8, 8), compression="gzip")
    g.create_dataset("HV", data=img * 0.5, chunks=(8, 8), compression="gzip")
    g.create_dataset("inputDataExceptionMask",
                     data=rng.integers(0, 3, (N_LINES, width), dtype=np.uint8))
    g.create_dataset("slantRange", data=r0 + dr * np.arange(width))
    g.create_dataset("slantRangeSpacing", data=dr)
    # uint32, as in NISAR products; line 0 has no valid sample, as at a frame start
    valid = np.tile([3, width - 2], (N_LINES, 1)).astype(np.uint32)
    valid[0] = 0
    g.create_dataset("validSamplesSubSwath1", data=valid)


def make_rslc(path, bands=("A", "B"), r0_b=R0, dr_b=DR_A * RATIO):
    """Write a synthetic RSLC with the given frequency groups."""
    with h5py.File(path, "w") as h:
        zt = h.create_dataset(f"{SWATHS}/zeroDopplerTime",
                              data=100.0 + np.arange(N_LINES) / 1520.0)
        zt.attrs["units"] = np.bytes_("seconds since 2026-06-22T00:00:00")
        _band(h.create_group(f"{SWATHS}/frequencyA"), W_A, DR_A, R0, 1)
        if "B" in bands:
            _band(h.create_group(f"{SWATHS}/frequencyB"), W_B, dr_b, r0_b, 2)
        h.create_dataset(LUT, data=np.arange(12.0).reshape(3, 4))
    return path


def window(line0, line1, sample0, sample1):
    return dict(line0=line0, line1=line1, sample0=sample0, sample1=sample1,
                full_length=N_LINES, full_width=W_A)


def crop(src, dst, win, freq="A", align_samples=1):
    with h5py.File(src, "r") as fin:
        bands = frequency_windows(fin, freq, win, align_samples)
        win = dict(win, frequencies=bands)
        with h5py.File(dst, "w") as fout:
            crop_copy(fin, fout, win)
    return bands


def test_the_window_snaps_to_whole_b_samples(tmp_path):
    src = make_rslc(tmp_path / "in.h5")
    with h5py.File(src, "r") as fin:
        bands = frequency_windows(fin, "A", window(5, 30, 21, 170))
    assert bands == {"A": dict(sample0=16, sample1=168, full_width=W_A),
                     "B": dict(sample0=2, sample1=21, full_width=W_B)}


def test_every_band_is_cut_to_one_slant_range_span(tmp_path):
    src, dst = make_rslc(tmp_path / "in.h5"), tmp_path / "out.h5"
    bands = crop(src, dst, window(5, 30, 21, 170))
    with h5py.File(src, "r") as fin, h5py.File(dst, "r") as fout:
        for f, b in bands.items():
            s0, s1 = b["sample0"], b["sample1"]
            g_in, g_out = fin[f"{SWATHS}/frequency{f}"], fout[f"{SWATHS}/frequency{f}"]
            for name in ("HH", "HV", "inputDataExceptionMask"):
                assert np.array_equal(g_out[name][()], g_in[name][5:30, s0:s1]), (f, name)
            assert np.array_equal(g_out["slantRange"][()], g_in["slantRange"][s0:s1])
            valid = g_out["validSamplesSubSwath1"]
            assert valid.dtype == g_in["validSamplesSubSwath1"].dtype
            # source rows are [3, width - 2): A's crop starts past 3, B's (at 2) before it
            expect = np.clip(np.array([3, b["full_width"] - 2]) - s0, 0, s1 - s0)
            assert expect.tolist() == {"A": [0, 152], "B": [1, 19]}[f]
            assert np.array_equal(valid[()], np.tile(expect, (25, 1)))
        ra = fout[f"{SWATHS}/frequencyA/slantRange"]
        rb = fout[f"{SWATHS}/frequencyB/slantRange"]
        assert rb[0] == pytest.approx(ra[0], abs=1e-6)
        assert rb[-1] == pytest.approx(ra[-RATIO], abs=1e-6)
        assert np.array_equal(fout[f"{SWATHS}/zeroDopplerTime"][()],
                              fin[f"{SWATHS}/zeroDopplerTime"][5:30])
        assert fout[f"{SWATHS}/zeroDopplerTime"].attrs["units"] == \
            fin[f"{SWATHS}/zeroDopplerTime"].attrs["units"]
        assert np.array_equal(fout[LUT][()], fin[LUT][()])


def test_the_far_edge_stops_at_the_shorter_band(tmp_path):
    src = make_rslc(tmp_path / "in.h5")
    with h5py.File(src, "r") as fin:
        bands = frequency_windows(fin, "A", window(0, N_LINES, 150, W_A))
    # A ends at 203, B at 26 * 8 = 208: the span ends at 200, the last whole B sample
    assert bands["A"]["sample1"] == 200 and bands["B"]["sample1"] == 25


def test_a_window_on_b_maps_onto_a(tmp_path):
    src = make_rslc(tmp_path / "in.h5")
    win = dict(window(0, N_LINES, 3, 20), full_width=W_B)
    with h5py.File(src, "r") as fin:
        bands = frequency_windows(fin, "B", win)
    assert bands["B"] == dict(sample0=3, sample1=20, full_width=W_B)
    assert bands["A"] == dict(sample0=24, sample1=160, full_width=W_A)


def test_a_single_band_product_keeps_its_window(tmp_path):
    src, dst = make_rslc(tmp_path / "in.h5", bands=("A",)), tmp_path / "out.h5"
    bands = crop(src, dst, window(0, 30, 21, 170))
    assert bands == {"A": dict(sample0=21, sample1=170, full_width=W_A)}
    with h5py.File(dst, "r") as fout:
        assert fout[f"{SWATHS}/frequencyA/HH"].shape == (30, 149)
        valid = fout[f"{SWATHS}/frequencyA/validSamplesSubSwath1"][()]
    # an empty line stays empty; the rest start at the crop's first sample
    assert valid[0].tolist() == [0, 0] and valid[1].tolist() == [0, 149]


def test_a_crop_inside_the_valid_edge_keeps_the_offset(tmp_path):
    src, dst = make_rslc(tmp_path / "in.h5", bands=("A",)), tmp_path / "out.h5"
    crop(src, dst, window(1, 30, 0, 100))
    with h5py.File(dst, "r") as fout:
        valid = fout[f"{SWATHS}/frequencyA/validSamplesSubSwath1"][()]
    assert valid[0].tolist() == [3, 100]


@pytest.mark.parametrize("kw, match", [
    (dict(r0_b=R0 + DR_A), "starts at"),
    (dict(dr_b=DR_A * 7.5), "not an integer multiple"),
])
def test_bands_off_one_lattice_are_refused(tmp_path, kw, match):
    src = make_rslc(tmp_path / "in.h5", **kw)
    with h5py.File(src, "r") as fin:
        with pytest.raises(ValueError, match=match):
            frequency_windows(fin, "A", window(5, 30, 21, 170))


def test_a_window_narrower_than_one_b_sample_is_refused(tmp_path):
    src = make_rslc(tmp_path / "in.h5")
    with h5py.File(src, "r") as fin:
        with pytest.raises(ValueError, match="no sample of the coarsest band"):
            frequency_windows(fin, "A", window(5, 30, 17, 23))


def test_the_start_moves_onto_the_sample_lattice_and_the_end_stays(tmp_path):
    src = make_rslc(tmp_path / "in.h5")
    with h5py.File(src, "r") as fin:
        bands = frequency_windows(fin, "A", window(0, N_LINES, 90, 150), align_samples=20)
    # 20 A samples and 8 per B sample: the start lands on 80 (a multiple of 40),
    # the end on 144, the last whole B sample before 150
    assert bands == {"A": dict(sample0=80, sample1=144, full_width=W_A),
                     "B": dict(sample0=10, sample1=18, full_width=W_B)}


def test_the_first_line_moves_onto_the_line_lattice():
    win = window(13, 37, 0, 64)
    assert align_lines(win, 6) == dict(win, line0=12)
    assert align_lines(win, 1) == win


def test_an_aligned_crop_holds_the_full_frame_pixels_of_its_lattice(tmp_path):
    src, dst = make_rslc(tmp_path / "in.h5"), tmp_path / "out.h5"
    win = align_lines(window(13, 37, 90, 150), 6)
    bands = crop(src, dst, win, align_samples=20)
    with h5py.File(src, "r") as fin, h5py.File(dst, "r") as fout:
        a_in, a_out = fin[f"{SWATHS}/frequencyA"], fout[f"{SWATHS}/frequencyA"]
        assert np.array_equal(a_out["HH"][()], a_in["HH"][12:37, 80:144])
        assert a_out["slantRange"][0] == a_in["slantRange"][80]
        assert np.array_equal(fout[f"{SWATHS}/frequencyB/HH"][()],
                              fin[f"{SWATHS}/frequencyB/HH"][12:37, 10:18])
    assert bands["A"]["sample0"] == 80


@pytest.mark.parametrize("n", [0, -3])
def test_a_lattice_below_one_is_refused(tmp_path, n):
    with pytest.raises(ValueError, match="alignment must be >= 1"):
        align_lines(window(0, 10, 0, 64), n)
    src = make_rslc(tmp_path / "in.h5")
    with h5py.File(src, "r") as fin:
        with pytest.raises(ValueError, match="alignment must be >= 1"):
            frequency_windows(fin, "A", window(0, 10, 0, 64), align_samples=n)
