"""Re-runnable check of the --radiometry products (writes JSON to stdout).

1. DN-mode reconversion vs the RSLC the RIFG runs used: which datasets differ.
2. beta0 vs DN pixels over a 2000 x 2000 window: power ratio and phase change.
3. GCOV-side readers on the beta0 product: beta0 LUT and noise product.
"""
import sys; sys.argv = [sys.argv[0]]
import json, re
import h5py, numpy as np
from nisar.products.readers import SLC
R = "/data/capella_mexico_city"; S = "science/LSAR/RSLC"
old, new, b0 = f"{R}/rslc/20240626.h5", f"{R}/rslc_beta0/dn_recheck_20240626.h5", f"{R}/rslc_beta0/20240626.h5"
out = {}
with h5py.File(old) as a, h5py.File(new) as b:
    same, diff = [], []
    def cmp(name, obj):
        if isinstance(obj, h5py.Dataset) and name in b and isinstance(b[name], h5py.Dataset):
            x, y = obj[()], b[name][()]
            eq = x.shape == y.shape and (np.array_equal(x, y, equal_nan=True) if x.dtype.kind in "fc" else np.array_equal(x, y))
            (same if eq else diff).append(name)
    a.visititems(cmp)
    only_new = []; b.visit(lambda n: only_new.append(n) if n not in a else None)
out["dn_mode_vs_rifg_input"] = {"identical_datasets": len(same), "differing": diff, "only_in_new": only_new}
with h5py.File(old) as a, h5py.File(b0) as c:
    zd = a[f"{S}/swaths/frequencyA/HH"][8000:10000, 4000:6000].astype(np.complex128)
    zb = c[f"{S}/swaths/frequencyA/HH"][8000:10000, 4000:6000].astype(np.complex128)
    nz = zd != 0
    ratio = np.abs(zb[nz]) ** 2 / np.abs(zd[nz]) ** 2
    xml = c[f"{S}/metadata/processingInformation/inputs/sicdXml"][()].decode()
    b00 = float(re.search(r'<BetaZeroSFPoly[^>]*>\s*<Coef exponent1="0" exponent2="0">([^<]+)<', xml).group(1))
    nb0 = c[f"{S}/metadata/calibrationInformation/frequencyA/noiseEquivalentBackscatter/HH"][()]
    ndn = h5py.File(new)[f"{S}/metadata/calibrationInformation/frequencyA/noiseEquivalentBackscatter/HH"][()]
out["beta0_vs_dn_window"] = {"pixels": int(nz.sum()), "power_ratio_min": float(ratio.min()),
    "power_ratio_max": float(ratio.max()), "BetaZeroSFPoly_00": b00,
    "max_abs_phase_change_rad": float(np.abs(np.angle(zb[nz] * np.conj(zd[nz]))).max()),
    "noise_dn2_db_min_max": [float(10 * np.log10(ndn.min())), float(10 * np.log10(ndn.max()))],
    "noise_beta0_db_min_max": [float(10 * np.log10(nb0.min())), float(10 * np.log10(nb0.max()))]}
s = SLC(hdf5file=b0); g = s.getRadarGrid()
out["gcov_readers_on_beta0"] = {"beta0_lut_at_scene_centre": s.getRadiometricCalibrationLUT("beta0").eval(g.sensing_mid, g.mid_range),
                                "noise_product_shape": list(s.getNoiseEquivalentBackscatter().power_linear.shape)}
print(json.dumps(out, indent=1))
