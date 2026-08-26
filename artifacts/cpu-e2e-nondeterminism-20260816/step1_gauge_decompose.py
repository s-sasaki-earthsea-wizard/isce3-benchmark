#!/usr/bin/env python3
"""Step 1 of isce3-benchmark #36: 2*pi gauge decomposition of the CPU
run-pair unwrappedPhase differences.

Hypothesis: the differences between same-config CPU runs are almost
entirely COMPONENT-WISE integer-2*pi re-referencing (a gauge change),
with genuine solution changes confined to a small pixel set near
component/branch-cut boundaries.

Uses the RADAR-GRID RUNW products (no geocoding interpolation smear).
For each run pair: d = unw_B - unw_A on jointly valid pixels; per
connected component (labels from run A), k_mode = modal round(d/2pi);
residual = d - 2*pi*k_mode. Reports gauge purity, flipped components,
unexplained pixels, and their coherence profile.
"""
import itertools

import h5py
import numpy as np

RUNS = {
    "control": "/ab/control/scratch/RUNW.h5",
    "treat": "/ab/treat/scratch/RUNW.h5",
    "run2": "/run2scratch/RUNW.h5",
}
G = "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/"
TWO_PI = 2.0 * np.pi
TOL = 1e-3  # rad — well above float32 phase noise, far below any real change


def load(path):
    with h5py.File(path, "r") as f:
        unw = f[G + "unwrappedPhase"][...]
        cc = f[G + "connectedComponents"][...]
        coh = f[G + "coherenceMagnitude"][...]
    return unw, cc, coh


data = {name: load(path) for name, path in RUNS.items()}

for a, b in itertools.combinations(RUNS, 2):
    ua, ca, coha = data[a]
    ub, cb, cohb = data[b]
    valid = np.isfinite(ua) & np.isfinite(ub) & (ca > 0) & (cb > 0)
    nv = int(valid.sum())
    d = ub.astype(np.float64) - ua.astype(np.float64)
    k = np.rint(d / TWO_PI).astype(np.int64)

    lab = ca[valid].astype(np.int64)
    kv = k[valid]
    kmin = kv.min()
    nk = int(kv.max() - kmin + 1)
    code = lab * nk + (kv - kmin)
    uc, cnt = np.unique(code, return_counts=True)
    ulab = uc // nk
    uk = uc % nk + kmin

    best = {}
    total = {}
    for l, kk, c in zip(ulab, uk, cnt):
        total[l] = total.get(l, 0) + c
        if l not in best or c > best[l][1]:
            best[l] = (kk, c)

    labs_u = np.unique(lab)
    kmode_arr = np.array([best[l][0] for l in labs_u], dtype=np.int64)
    purity_arr = np.array([best[l][1] / total[l] for l in labs_u])
    size_arr = np.array([total[l] for l in labs_u])

    kmode_pix = kmode_arr[np.searchsorted(labs_u, lab)]
    resid = d[valid] - kmode_pix * TWO_PI
    outlier = np.abs(resid) > TOL
    n_out = int(outlier.sum())
    hard = int((np.abs(resid) > np.pi).sum())

    coh_mean = 0.5 * (coha + cohb)
    coh_valid = coh_mean[valid]

    kmode_hist = {}
    for km, sz in zip(kmode_arr, size_arr):
        kmode_hist.setdefault(int(km), [0, 0])
        kmode_hist[int(km)][0] += 1
        kmode_hist[int(km)][1] += int(sz)

    print(f"== {a} vs {b} ==")
    print(f"valid px (finite & CC>0 both): {nv}/{ua.size}")
    print(f"components (labels from {a}): {labs_u.size}")
    print(f"k_mode histogram {{k: (n_comp, n_px)}}: {kmode_hist}")
    print(f"components with k_mode != 0 (re-referenced): "
          f"{int((kmode_arr != 0).sum())}")
    print(f"components with gauge purity < 99.9%: "
          f"{int((purity_arr < 0.999).sum())} "
          f"(min purity {purity_arr.min():.6f}, "
          f"at size {int(size_arr[purity_arr.argmin()])})")
    print(f"gauge-unexplained px (|resid| > {TOL:g} rad): {n_out} "
          f"({n_out / nv:.3e});  hard disagreements (|resid| > pi): {hard}")
    if n_out:
        print(f"  resid over outliers: max|.|={np.abs(resid[outlier]).max():.4f} "
              f"median|.|={np.median(np.abs(resid[outlier])):.4f}")
        print(f"  coherence: outlier median={np.median(coh_valid[outlier]):.3f} "
              f"vs all-valid median={np.median(coh_valid):.3f}")
    keep = ~outlier
    print(f"gauge-explained residual RMS (non-outlier px): "
          f"{np.sqrt(np.mean(resid[keep] ** 2)):.3e} rad")
    print()
