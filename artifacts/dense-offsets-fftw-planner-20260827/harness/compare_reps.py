#!/usr/bin/env python3
"""Compare unwrap-step replicates (bench#36 Step 2, Phase A).

Host-side. For each replicate:

  1. sha256 of retained scratch intermediates (crossmul, unwrap, rdr2geo).  With the overlay's
     `delete_scratch=False`, snaphu-py leaves behind the exact bytes SNAPHU
     consumed and produced:
         snaphu.igram.<rand>.c8   complex interferogram   (solver input)
         snaphu.corr.<rand>.f4    coherence               (solver input)
         snaphu.mask.<rand>.u1    mask                    (solver input)
         snaphu.unw.<rand>.f4     unwrapped phase         (solver output)
         snaphu.conncomp.<rand>.u4  connected components  (solver output)
         snaphu.config.<rand>.txt  config                 (contains the
                                                           random paths)
     The <rand> token comes from `new_unique_file()` and differs every run,
     so filenames are canonicalised before comparison and the config file is
     compared after normalising the paths it embeds.

  2. Dataset- and attribute-level comparison of RUNW.h5 across replicates.

Decision tree (PREREGISTRATION.md Phase A):
  - solver inputs identical + solver outputs identical + RUNW identical
        -> unwrap step deterministic; flips originate upstream. H1, H2 rejected.
  - solver inputs identical + solver outputs differ
        -> H1: SNAPHU itself is unstable.
  - solver inputs differ
        -> H2: crossmul@13x16 / preprocess nondeterminism upstream of SNAPHU.
  - solver outputs identical + RUNW differs
        -> post-solver stage (bridge_unwrapped_phase / stats) in scope.
"""
import hashlib
import os
import re
import sys
from pathlib import Path

import h5py
import numpy as np

BASE = Path(os.environ.get("STEP2_BASE",
            os.path.expanduser("~/scratch/bench36_step2_20260826")))
REPS = [int(a) for a in sys.argv[1:]] or [1, 2, 3]

IG = "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH"
PO = "science/LSAR/RUNW/swaths/frequencyA/pixelOffsets/HH"
DATASETS = [
    f"{IG}/unwrappedPhase",
    f"{IG}/connectedComponents",
    f"{IG}/coherenceMagnitude",
    "science/LSAR/RUNW/swaths/frequencyA/interferogram/mask",
    f"{PO}/alongTrackOffset",
    f"{PO}/slantRangeOffset",
    f"{PO}/correlationSurfacePeak",
    "science/LSAR/RUNW/swaths/frequencyA/pixelOffsets/digitalElevationModel",
]
ATTRS = ["mean_value", "min_value", "max_value", "sample_stddev"]

SOLVER_IN = ("snaphu.igram", "snaphu.corr", "snaphu.mask")
SOLVER_OUT = ("snaphu.unw", "snaphu.conncomp")

# snaphu.<kind>.<rand>.<ext>  ->  snaphu.<kind>.<ext>
UNIQ = re.compile(r"^(snaphu\.[a-z]+)\.[^.]+\.([a-z0-9]+)$")


def canon(name):
    m = UNIQ.match(name)
    return f"{m.group(1)}.{m.group(2)}" if m else name


def sha256_file(p, blocksize=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(blocksize), b""):
            h.update(b)
    return h.hexdigest()


def sha256_config(p):
    """Hash the config with the run-unique paths normalised away."""
    text = p.read_text()
    text = re.sub(r"(snaphu\.[a-z]+)\.[^.\s]+\.([a-z0-9]+)", r"\1.\2", text)
    text = re.sub(r"/scratch/unwrap/\S*?/", "<dir>/", text)
    return hashlib.sha256(text.encode()).hexdigest(), text


def scratch_hashes(rep):
    root = BASE / f"rep{rep}" / "scratch"
    out, configs = {}, {}
    # rdr2geo is included because InSAR_L1_writer regenerates
    # <Type>_{offsets,ifgram}_dem.rdr per replicate (everything else in that
    # directory is a symlink into the shared phase0 scratch and is skipped),
    # which incidentally tests CPU Topo determinism.
    for sub in ("crossmul", "unwrap", "rdr2geo"):
        d = root / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            key = str(p.relative_to(root).parent / canon(p.name))
            if p.name.startswith("snaphu.config."):
                h, txt = sha256_config(p)
                out[key], configs[key] = h, txt
            else:
                out[key] = sha256_file(p)
    return out, configs


print(f"# unwrap-step replicate comparison  (reps: {REPS})\n")

# --- 1. scratch intermediates ---------------------------------------------
allh, allcfg = {}, {}
for r in REPS:
    allh[r], allcfg[r] = scratch_hashes(r)

keys = sorted(set().union(*[set(h) for h in allh.values()]))
same, diff = [], []
for k in keys:
    vals = [allh[r].get(k) for r in REPS]
    if any(v is None for v in vals):
        diff.append((k, "MISSING in some replicate"))
    elif len(set(vals)) == 1:
        same.append(k)
    else:
        diff.append((k, "sha256 DIFFERS"))

print(f"scratch intermediates: {len(same)} identical, {len(diff)} differing")
for k in same:
    print(f"  same {k}")
for k, why in diff:
    print(f"  DIFF {k}: {why}")


def classify(prefixes):
    """(n_identical, n_differing) over files whose canonical name matches."""
    ident = sum(1 for k in same if Path(k).name.startswith(prefixes))
    dif = sum(1 for k, _ in diff if Path(k).name.startswith(prefixes))
    return ident, dif


in_same, in_diff = classify(SOLVER_IN)
out_same, out_diff = classify(SOLVER_OUT)
print(f"\n  SNAPHU solver inputs : {in_same} identical, {in_diff} differing")
print(f"  SNAPHU solver outputs: {out_same} identical, {out_diff} differing")

# --- 2. RUNW datasets ------------------------------------------------------
print("\nRUNW dataset comparison (rep1 as reference):")
files = {r: h5py.File(BASE / f"rep{r}" / "out" / "RUNW.h5", "r") for r in REPS}
ref = REPS[0]
runw_identical = True
for ds in DATASETS:
    a = files[ref][ds][()]
    verdict = []
    for r in REPS[1:]:
        b = files[r][ds][()]
        if a.tobytes() == b.tobytes():
            verdict.append(f"rep{r}:identical")
        else:
            runw_identical = False
            d = np.abs(a.astype(np.float64) - b.astype(np.float64))
            verdict.append(f"rep{r}:DIFFERS n={np.count_nonzero(d)} "
                           f"max={np.nanmax(d):.3e}")
    print(f"  {ds.split('/RUNW/')[-1]}: " + ", ".join(verdict))

# CC-conditional detail for unwrappedPhase if it differs
uw, cc = f"{IG}/unwrappedPhase", f"{IG}/connectedComponents"
a = files[ref][uw][()]
for r in REPS[1:]:
    b = files[r][uw][()]
    if a.tobytes() == b.tobytes():
        continue
    ca, cb = files[ref][cc][()], files[r][cc][()]
    valid = (ca > 0) & (cb > 0)
    d = np.abs(a.astype(np.float64) - b.astype(np.float64))
    inv = ~valid
    print(f"\n  unwrappedPhase rep{ref} vs rep{r} (CC-conditional):")
    print(f"    CC>0 both : n_diff={np.count_nonzero(d[valid])} "
          f"max={d[valid].max() if valid.any() else 0:.3e} "
          f"n(|d|>pi)={np.count_nonzero(d[valid] > np.pi)}")
    print(f"    CC==0 any : n_diff={np.count_nonzero(d[inv])} "
          f"max={d[inv].max() if inv.any() else 0:.3e} "
          f"n(|d|>pi)={np.count_nonzero(d[inv] > np.pi)}")
    print(f"    CC label maps identical: {np.array_equal(ca, cb)}")

# --- 3. statistics attributes ---------------------------------------------
# isce3::math::Stats merges per-thread partials under `#pragma omp critical`
# (cxx/isce3/math/Stats.cpp:161), so merge order -- and therefore the rounding
# of mean/stddev -- depends on thread arrival order. Pixel arrays are
# unaffected; these attributes need not be. Checked separately from the data.
print("\nStatistics attributes (rep1 as reference):")
attrs_identical = True
for ds in DATASETS:
    ao = files[ref][ds]
    for r in REPS[1:]:
        bo = files[r][ds]
        for at in ATTRS:
            if at not in ao.attrs or at not in bo.attrs:
                continue
            av, bv = ao.attrs[at], bo.attrs[at]
            if np.array_equal(av, bv):
                continue
            attrs_identical = False
            print(f"  DIFF {ds.split('/RUNW/')[-1]}.{at} "
                  f"rep{ref}={av} rep{r}={bv}")
if attrs_identical:
    print("  all compared attributes identical")

# --- verdict ---------------------------------------------------------------
print("\nVERDICT:")
if in_diff:
    print("  H2 ACCEPTED: SNAPHU solver inputs differ across replicates ->"
          " crossmul@13x16 / preprocess nondeterminism upstream of the solver.")
elif out_diff:
    print("  H1 ACCEPTED: solver inputs byte-identical but SNAPHU outputs"
          " differ -> the solver itself is nondeterministic.")
elif not runw_identical:
    print("  Solver deterministic, RUNW differs -> post-solver stage"
          " (bridge_unwrapped_phase / stats write-back) in scope.")
else:
    print("  H1 and H2 REJECTED: the unwrap step is fully deterministic on"
          " fixed inputs -> E2E flips originate upstream of unwrap."
          " Proceed to Phase B/C.")
    if not attrs_identical:
        print("  NOTE: pixel arrays identical but statistics attributes"
              " differ -> Stats.cpp omp-critical merge order.")

for f in files.values():
    f.close()
