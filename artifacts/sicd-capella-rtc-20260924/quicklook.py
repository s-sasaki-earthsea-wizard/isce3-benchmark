import sys; sys.argv = [sys.argv[0]]
sys.path.insert(0, "/work/tools")
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from compare_rtc import read_gcov, read_tif, _overlap
R = "/data/capella_mexico_city"
gc = read_gcov(f"{R}/gcov/20240626/gcov_20240626.h5")
fig, ax = plt.subplots(1, 3, figsize=(15, 5.2))
a0 = gc["gamma0"]; ok = np.isfinite(a0) & (a0 > 0)
ax[0].imshow(np.where(ok, 10 * np.log10(np.where(ok, a0, 1)), np.nan)[::4, ::4], cmap="gray", vmin=-20, vmax=5)
ax[0].set_title("isce3 GCOV gamma0 [dB], 2024-06-26, 5 m")
for k, v in ((1, "matched"), (2, "matched-tfix-rfix")):
    stem = "CAPELLA_C14_SM_SICD_HH_20240626150051_20240626150055"
    b, xb, yb = read_tif(f"{R}/multirtc/20240626/{v}/output/{stem}.tif")
    sg, sm = _overlap(gc["x"], gc["y"], xb, yb)
    a, bb = gc["gamma0"][sg], b[sm]
    m = np.isfinite(a) & np.isfinite(bb) & (a > 0) & (bb > 0)
    d = np.where(m, 10 * np.log10(np.where(m, bb, 1) / np.where(m, a, 1)), np.nan)
    im = ax[k].imshow(d[::4, ::4], cmap="RdBu_r", vmin=-6 if k == 1 else -0.01, vmax=6 if k == 1 else 0.01)
    ax[k].set_title(f"MultiRTC {v} - GCOV [dB]")
    fig.colorbar(im, ax=ax[k], fraction=0.046)
for x in ax: x.set_xticks([]); x.set_yticks([])
fig.tight_layout(); fig.savefig(f"{R}/rtc_compare/quicklook_20240626.png", dpi=80)
print("saved")
