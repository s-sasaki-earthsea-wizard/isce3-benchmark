#!/usr/bin/env python3
"""Step 1 addendum: confirm the ±2pi run-pair differences live in the
CC==0 (officially unreliable) region, and check how far they propagate
into the ionosphere phase screen."""
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


def load(path):
    with h5py.File(path, "r") as f:
        unw = f[G + "unwrappedPhase"][...]
        cc = f[G + "connectedComponents"][...]
        iono = f[G + "ionospherePhaseScreen"][...]
    return unw, cc, iono


data = {name: load(path) for name, path in RUNS.items()}

for a, b in itertools.combinations(RUNS, 2):
    ua, ca, ia = data[a]
    ub, cb, ib = data[b]
    print(f"== {a} vs {b} ==")

    for region, mask in [
        ("CC>0 both", (ca > 0) & (cb > 0)),
        ("CC==0 either", (ca == 0) | (cb == 0)),
    ]:
        m = mask & np.isfinite(ua) & np.isfinite(ub)
        d = (ub.astype(np.float64) - ua.astype(np.float64))[m]
        neq = int((d != 0).sum())
        jumps = int((np.abs(d) > np.pi).sum())
        line = (f"  unw [{region}]: px={int(m.sum())} differing={neq} "
                f"|d|>pi: {jumps}")
        if d.size:
            line += f" max|d|={np.abs(d).max():.4g}"
        if jumps:
            k = np.rint(d[np.abs(d) > np.pi] / TWO_PI)
            vals, cnts = np.unique(k, return_counts=True)
            line += f" k-hist={dict(zip(vals.astype(int), cnts))}"
        print(line)

    m = np.isfinite(ia) & np.isfinite(ib)
    d = (ib.astype(np.float64) - ia.astype(np.float64))[m]
    mcc = m & (ca > 0) & (cb > 0)
    dcc = (ib.astype(np.float64) - ia.astype(np.float64))[mcc]
    print(f"  ionoScreen [all finite]: differing={int((d != 0).sum())}"
          f"/{d.size} max|d|={np.abs(d).max():.4g} rms={np.sqrt(np.mean(d**2)):.4g}")
    print(f"  ionoScreen [CC>0 both]: differing={int((dcc != 0).sum())}"
          f"/{dcc.size} max|d|={np.abs(dcc).max():.4g} "
          f"rms={np.sqrt(np.mean(dcc**2)):.4g}")
    print()
