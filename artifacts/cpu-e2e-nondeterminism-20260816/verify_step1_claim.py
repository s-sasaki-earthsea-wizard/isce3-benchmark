#!/usr/bin/env python3
"""Independent spot-check of Karasunoendou's Step 1 numbers (control vs
treat RUNW): on CC>0 common support the diffs should be ~1e-5 rad with
zero |d|>pi pixels; +-2pi flips confined to CC==0; single component."""
import h5py
import numpy as np

B = "/ab/control/scratch/RUNW.h5"
T = "/ab/treat/scratch/RUNW.h5"
P = "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/"

with h5py.File(B, "r") as fb, h5py.File(T, "r") as ft:
    ub = fb[P + "unwrappedPhase"][...].astype(np.float64)
    ut = ft[P + "unwrappedPhase"][...].astype(np.float64)
    cb = fb[P + "connectedComponents"][...]
    ct = ft[P + "connectedComponents"][...]

d = ut - ub
valid = (cb > 0) & (ct > 0)
inv = ~valid
print(f"labels_control={np.unique(cb)}  labels_treat={np.unique(ct)}")
print(f"common_valid_px={int(valid.sum())}")
dv = d[valid]
print(f"CC>0:  n_diff={(dv != 0).sum()}  max|d|={np.abs(dv).max():.4g} rad  "
      f"rms={np.sqrt(np.mean(dv**2)):.4g}  n(|d|>pi)={(np.abs(dv) > np.pi).sum()}")
di = d[inv & np.isfinite(d)]
k = np.round(di / (2 * np.pi))
print(f"CC==0: n={di.size}  n(|k|>=1)={(np.abs(k) >= 1).sum()}  "
      f"k_values={np.unique(k[np.abs(k) >= 1])}  max|d|={np.abs(di).max():.4g}")
