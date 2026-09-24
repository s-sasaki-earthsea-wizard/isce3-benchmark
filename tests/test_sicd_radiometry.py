"""Tests for the --radiometry option of tools/sicd_to_nisar_rslc.py.

Runs inside the dev container (the converter imports isce3). Uses small
synthetic arrays in place of the NITF memmap and an in-memory HDF5 file, so
no SICD download is needed. The polynomial is deliberately non-constant in
both coordinates: the real Capella BetaZeroSFPoly is constant to ~1e-12, which
would hide a coordinate, flip or block-offset mistake.
"""

import h5py
import numpy as np
import pytest

sicd_to_nisar_rslc = pytest.importorskip("sicd_to_nisar_rslc")

from sicd_to_nisar_rslc import (SWATHS, beta0_factor, image_xy, lut_xy,  # noqa: E402
                                write_image)

N_RG, N_AZ = 37, 53
SCP_ROW, SCP_COL = 17, 29
SS_RG, SS_AZ = 0.6, 1.1
# B(x, y) = 1e-2 + 1e-4 x + 1.2e-4 y + 1e-6 x y + 5e-6 x^2 over x in [-10.2, 11.4] m,
# y in [-31.9, 26.4] m: positive everywhere (min ~4.7e-3) and varying by ~100 %.
BETA_POLY = np.array([[1e-2, 1.2e-4, 0.0],
                      [1e-4, 1e-6, 0.0],
                      [5e-6, 0.0, 0.0]])


def _grid(flip, radiometry, beta_poly=BETA_POLY):
    return {
        "n_rg": N_RG, "n_az": N_AZ, "flip": flip, "pol": "HH",
        "scp_row": SCP_ROW, "scp_col": SCP_COL, "ss_rg": SS_RG, "ss_az": SS_AZ,
        "beta_poly": beta_poly, "radiometry": radiometry,
        # only used by the spectral diagnostics
        "row_kctr": 64.0, "row_dkcoa": 0.0, "row_bw_cpm": 1.0, "row_sgn": "+1",
        "col_dkcoa": 0.0, "col_bw_cpm": 0.7, "col_sgn": "+1", "prf_grid": 6000.0,
    }


def _raw(seed=0):
    rng = np.random.default_rng(seed)
    raw = rng.integers(-300, 300, size=(N_RG, N_AZ, 2)).astype(">i2")
    raw[-3:, :, :] = 0            # zero-filled far range, like the Capella scenes
    return raw


def _run(raw, flip, radiometry, block_lines=7, beta_poly=BETA_POLY):
    g = _grid(flip, radiometry, beta_poly)
    with h5py.File("mem.h5", "w", driver="core", backing_store=False) as fid:
        diag = write_image(fid, raw, g, block_lines=block_lines,
                           spectrum_lines=5, compress=False)
        img = fid[f"{SWATHS}/frequencyA/HH"][()]
        valid = fid[f"{SWATHS}/frequencyA/validSamplesSubSwath1"][()]
    # h5py maps the (r, i) float32 compound to complex64 on read
    z = img.astype(np.complex128) if img.dtype.names is None else \
        img["r"].astype(np.float64) + 1j * img["i"].astype(np.float64)
    return z, valid, diag


def _expected_dn(raw, flip):
    zr = raw[..., 0].astype(np.float64) + 1j * raw[..., 1].astype(np.float64)  # (rg, az)
    zr = zr.T                                                                   # (az, rg)
    return zr[::-1, :] if flip else zr


def _sicd_col_of_line(flip):
    lines = np.arange(N_AZ)
    return N_AZ - 1 - lines if flip else lines


@pytest.mark.parametrize("flip", [False, True])
def test_dn_mode_is_a_pure_transpose(flip):
    raw = _raw()
    z, valid, diag = _run(raw, flip, "dn")
    np.testing.assert_array_equal(z, _expected_dn(raw, flip))
    assert diag["radiometry"] == "dn"
    assert diag["beta0_amplitude_scale_min_max"] is None
    assert np.all(valid[:, 1] == N_RG - 3)


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize("block_lines", [1, 7, 64])
def test_beta0_scales_each_pixel_at_its_own_sicd_coordinates(flip, block_lines):
    raw = _raw(1)
    z, _, diag = _run(raw, flip, "beta0", block_lines=block_lines)
    zdn = _expected_dn(raw, flip)
    # expected factor at (line, sample) = B(xrow(sample), ycol(SICD col of line))
    x, y = image_xy(_grid(flip, "beta0"), np.arange(N_RG), _sicd_col_of_line(flip))
    b = beta0_factor(_grid(flip, "beta0"), x, y).T      # (az, rg)
    assert np.ptp(b) / b.mean() > 0.5, "test polynomial must vary strongly"
    np.testing.assert_allclose(z, zdn * np.sqrt(b), rtol=2e-6, atol=0)
    nz = zdn != 0
    # phase untouched, power ratio == B
    np.testing.assert_allclose(np.angle(z[nz]), np.angle(zdn[nz]), atol=2e-6)
    np.testing.assert_allclose(np.abs(z[nz]) ** 2 / np.abs(zdn[nz]) ** 2, b[nz], rtol=5e-6)
    assert np.all(z[~nz] == 0)
    lo, hi = diag["beta0_amplitude_scale_min_max"]
    assert lo == pytest.approx(np.sqrt(b.min()), rel=1e-6)
    assert hi == pytest.approx(np.sqrt(b.max()), rel=1e-6)


def test_valid_samples_do_not_depend_on_radiometry():
    raw = _raw(2)
    _, v_dn, _ = _run(raw, True, "dn")
    _, v_b0, _ = _run(raw, True, "beta0")
    np.testing.assert_array_equal(v_dn, v_b0)


def test_nonpositive_beta_factor_is_rejected():
    bad = BETA_POLY.copy()
    bad[0, 0] = -1.0
    with pytest.raises(SystemExit, match="BetaZeroSFPoly"):
        _run(_raw(), False, "beta0", beta_poly=bad)


def test_lut_mapping_matches_the_image_coordinate_mapping():
    # A pixel's (zero-Doppler time, slant range) must map back to the same
    # (xrow, ycol) that its (row, col) indices give -- for either time slope sign.
    for slope in (-1.5e-4, +1.5e-4):
        g = _grid(slope < 0, "beta0")
        g.update(r_ca_scp=764555.9, cs_offset=54051.3, time_ca=np.array([2.13, slope]))
        rows = np.array([0, SCP_ROW, N_RG - 1])
        cols = np.array([0, SCP_COL, N_AZ - 1])
        x_img, y_img = image_xy(g, rows, cols)
        rng = g["r_ca_scp"] + (rows - SCP_ROW) * SS_RG
        t = g["cs_offset"] + g["time_ca"][0] + g["time_ca"][1] * (cols - SCP_COL) * SS_AZ
        x_lut, y_lut = lut_xy(g, t[None, :], rng[:, None])
        np.testing.assert_allclose(x_lut, np.broadcast_to(x_img, x_lut.shape), atol=1e-6)
        np.testing.assert_allclose(y_lut, np.broadcast_to(y_img, y_lut.shape), atol=1e-6)


def _lut_grid(flip):
    g = _grid(flip, "beta0")
    slope = -1.5e-4 if flip else 1.5e-4
    g.update(r_ca_scp=764555.9, cs_offset=54051.3, time_ca=np.array([2.13, slope]),
             noise_type="ABSOLUTE",
             # dB: strongly quadratic in range, like the Capella NoisePoly
             noise_poly=np.array([[22.0, 0.01, 0.0], [0.05, 0.0, 0.0], [0.02, 0.0, 0.0]]))
    return g


@pytest.mark.parametrize("flip", [False, True])
def test_noise_lut_is_held_at_the_image_edge_outside_the_image(flip):
    from sicd_to_nisar_rslc import noise_power_dn2, radiometric_luts
    g = _lut_grid(flip)
    x_edge = np.array([(0 - SCP_ROW) * SS_RG, (N_RG - 1 - SCP_ROW) * SS_RG])
    # LUT grid padded 50 m beyond the image in range and 0.05 s in time
    rng = g["r_ca_scp"] + np.linspace(x_edge[0] - 50, x_edge[1] + 50, 41)
    t_img = g["cs_offset"] + g["time_ca"][0] + g["time_ca"][1] * (np.array([0, N_AZ - 1]) - SCP_COL) * SS_AZ
    az = np.linspace(t_img.min() - 0.05, t_img.max() + 0.05, 31)
    noise_b0, desc = radiometric_luts(g, az, rng)
    assert np.all(np.isfinite(noise_b0)) and np.all(noise_b0 > 0)
    assert "beta0 power" in desc and "nearest image edge" in desc
    # nodes beyond the far/near range edge equal the value at that edge
    x, _ = image_xy(g, [0, N_RG - 1], [SCP_COL])
    near_far = noise_power_dn2(g, x, np.zeros_like(x)) * beta0_factor(g, x, np.zeros_like(x))
    t_scp = g["cs_offset"] + g["time_ca"][0]
    i = np.argmin(np.abs(az - t_scp))
    y_i = (az[i] - g["cs_offset"] - g["time_ca"][0]) / g["time_ca"][1]
    near = noise_power_dn2(g, np.array([x_edge[0]]), np.array([y_i])) * beta0_factor(g, np.array([x_edge[0]]), np.array([y_i]))
    far = noise_power_dn2(g, np.array([x_edge[1]]), np.array([y_i])) * beta0_factor(g, np.array([x_edge[1]]), np.array([y_i]))
    np.testing.assert_allclose(noise_b0[i, 0], near[0], rtol=1e-12)
    np.testing.assert_allclose(noise_b0[i, -1], far[0], rtol=1e-12)
    assert near_far.shape == (2, 1)
    # dn mode differs from beta0 mode by exactly the beta0 factor
    g_dn = dict(g, radiometry="dn")
    noise_dn, desc_dn = radiometric_luts(g_dn, az, rng)
    assert "DN^2" in desc_dn
    assert np.all(noise_b0 < noise_dn)
