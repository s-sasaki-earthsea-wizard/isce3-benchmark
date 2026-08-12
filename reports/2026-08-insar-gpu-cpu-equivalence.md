# NISAR insar workflow (RSLC -> GUNW): CPU vs GPU equivalence and speedup

- Date: 2026-08-10 (measurements; ported here 2026-08-13)
- Issue: [isce3-benchmark#26](https://github.com/s-sasaki-earthsea-wizard/isce3-benchmark/issues/26)
- Host: nucbox-evo-t1 — RTX 5080 16 GiB (sm_120), 16-thread host;
  reference — A100-SXM4-80GB Runpod pod (sm_80)
- isce3 commit: v0.25.16 (upstream tag, same as the ASF PGE),
  from-source CUDA build
- Runconfig: ASF-replica (`configs/insar_gunw_ASC139_019_20260705_20260717.yaml`
  in the private `nisar-displacement` repo); GPU variant differs
  **only** in `worker.gpu_enabled: true` (`..._gpu.yaml`). All
  block/tile knobs identical.
- Dataset: NISAR L1 RSLC pair, ASC 139/019,
  2026-07-05 -> 2026-07-17 (Boso), freq A HH (ASF DAAC, Earthdata
  login required; ~2 GB per granule, not tracked in git)

> **Port note.** This report was originally published 2026-08-10 in
> the private `nisar-displacement` repo (where the runs were driven)
> and revised there on 2026-08-12
> ([PR #24](https://github.com/s-sasaki-earthsea-wizard/nisar-displacement/pull/24),
> the unwrap/crossmul containment correction). It moved here because
> its subject is isce3 performance, which is this repo's charter —
> `nisar-displacement` intentionally does not depend on isce3. The
> pre-port revision history lives in that repo
> (`reports/2026-08-10-gpu-cpu-equivalence.md`, last pre-port commit
> `8591d04`). Timing evidence: [`artifacts/insar-timing-20260810/`](../artifacts/insar-timing-20260810/).

## Headline results

1. **The GPU path is fully deterministic**: two independent GPU runs
   produced **bit-identical products** (every layer diff exactly 0.0,
   byte-identical file sizes).
2. **The CPU path is not**: two CPU runs differ at a small but nonzero
   floor (coherence RMS 3.1e-8, unwrapped wrapped-RMS 0.019 rad,
   3.6e-5 of pixels with a 2π flip) — consistent with OpenMP
   reduction-order nondeterminism. The ASF-distributed granule agrees
   with our CPU runs at this same floor (validated 2026-08-06).
3. **GPU and CPU disagree far above both floors.** GPU vs CPU:
   - unwrappedPhase: wrapped RMS **0.311 rad**, **7.55 %** of pixels
     ≥ π about the median (CPU floor: 0.019 rad / 0.0036 %);
   - coherenceMagnitude: RMS **2.1e-3** (CPU floor 3.1e-8 — 5 orders);
   - ionospherePhaseScreen: **RMS about median 10.0 rad** (spatially
     varying). The ~37.9 rad median offset is ≈ 6.03 cycles — close
     enough to an integer cycle count that it likely contains a
     constant unwrap-ambiguity component, which is physically
     meaningless for a relative screen; the spatial RMS is the
     meaningful figure;
   - mask: 13 vs 11 labels, 42 % of labeled pixels differ.
4. **The divergence is established before unwrapping.** The radar-grid
   RIFG intermediates show wrapped-igram phase diff RMS 0.16 rad
   (92.6 % of pixels > 0.01 rad) and ~15 % RMS relative magnitude
   difference. Everything downstream (SNAPHU region choices, the iono
   screen's unwrap/bridge decisions) amplifies this.
5. End-to-end wall time: **GPU 3843–3960 s vs CPU 6176 s (1.56–1.61x)**
   on the same host.

Product-level comparisons (in the `nisar-displacement` checkout):
`out/gunw_gpu_run1/compare_vs_cpu.json`, `compare_vs_asf.json`,
`compare_gpu1_vs_gpu2.json`,
`out/gunw_cpu_run2/compare_cpu2_vs_cpu1.json`.

## Interpretation

The CPU implementation is corroborated independently (two local runs +
the ASF production run agree at the numerical-noise floor), while the
GPU implementation is self-consistent but systematically different.
The GPU-vs-CPU difference is therefore a **code-path divergence**, not
run-to-run noise, and not hardware-specific flakiness (the GPU result
is exactly reproducible).

Prime suspect ordering (not yet localized):

1. **dense offsets: `cuAmpcor` vs the CPU ampcor** — genuinely
   different implementations. Caveat (team review 2026-08-10): the
   substantive basis for ranking this first is its **position** as
   the topmost independently-implemented stage in the chain, not the
   15 % magnitude figure — a multilooked igram's magnitude includes
   the coherence, so misregistration from *any* upstream stage
   (offsets, resample, crossmul) depresses it equally; and in the
   full run cuAmpcor's own input SLC is already GPU-produced, so
   nothing here isolates Ampcor. Only the same-input replay
   experiments below can discriminate. (An earlier draft cited the
   rubbersheet stage running 1.9x slower on GPU offsets as a third
   hint; retracted — a third GPU run with bit-identical inputs took
   224 s vs the CPU's 218 s, so the 322-408 s runs were host-side
   walltime variance, not data-driven.)
2. Resample interpolation (GPU vs CPU sinc kernels).
3. Crossmul (GPU FFT oversampling/filter path).

Localization requires runs with
`intermediate_files_removal_enabled: false` and stage-wise numeric
comparison (or controlled experiments with pinned offsets) — planned
as the follow-up.

## Per-stage wall times (clean logs, NVMe-backed)

CPU = 16 threads; GPU = same host, RTX 5080. The stage timers do not
all sit at the same level: some stages invoke other timed stages while
their own timer is running (`phase unwrapping` re-runs `crossmul` at
the unwrap look factor; the ionosphere chain's `prepare_insar_hdf5`
re-runs `rdr2geo`/`geo2rdr`). Such occurrences are **children** of
their enclosing row, not siblings. Child rows are indented below their
parent; summing the table means summing parents only (or children only
within a parent), never both. The tree is machine-derived by
`tools/parse_insar_timing.py` from the `starting X` /
`successfully ran X` bracket pairs in both logs.

| stage | CPU | GPU-5080 | CPU/GPU |
|---|---|---|---|
| rdr2geo | 991.3 | 265.9 | 3.73x |
| geo2rdr | 183.5 | 128.3 | 1.43x |
| prepare_insar_hdf5 | 557.4 | 562.5 | 0.99x |
| resample (coarse) | 327.5 | 183.1 | 1.79x |
| dense_offsets | 608.7 | 164.8 | 3.69x |
| polyfit rubbersheet † | 218.3 | 407.6 | 0.54x |
| resample #2 (fine) | 497.9 | 472.2 | 1.05x |
| crossmul (RIFG 5x6) | 542.3 | 209.0 | 2.59x |
| phase unwrapping, whole stage ‡ | 1441.9 | 1090.1 | 1.32x |
| └ crossmul (13x16), nested ‡ | 514.7 | 184.7 | **2.79x** |
| └ SNAPHU proper ‡ | 927.2 | 905.4 | **1.02x** |
| iono chain, all self times § | 449.0 | 264.5 | 1.70x |
| └ Ionosphere proper (estimation + filter) § | 92.7 | 65.8 | 1.41x |
| └ prepare_insar_hdf5 #2 § | 189.8 | 89.2 | 2.13x |
| &nbsp;&nbsp;└ rdr2geo #2, nested § | 126.3 | 40.3 | 3.13x |
| &nbsp;&nbsp;└ geo2rdr #2, nested § | 25.2 | 10.4 | 2.43x |
| └ resample #3 | 62.5 | 44.6 | 1.40x |
| └ crossmul #3 | 48.0 | 23.0 | 2.08x |
| └ phase unwrapping #2 | 56.0 | 41.7 | 1.34x |
| geocode (+wrapped igram) | 255.4 | 113.0 | 2.26x |
| solid earth tides + baseline | 41.8 | 43.3 | 0.97x |
| **INSAR total** | **6175.7** | **3959.7** | **1.56x** |

† rubbersheet is CPU-only and its inputs are bit-identical across GPU
runs, yet its walltime varied 224-408 s over three runs (host
variance; the retraction in "Interpretation" applies). The 407.6 s
cell is run #2's log value, at the unlucky end of that range — run #3
took 224.4 s, i.e. parity with the CPU run. Do not read a
data-driven slowdown into this row.

‡ **Correction (2026-08-12).** An earlier revision of this table listed
`crossmul #2 (unwrap 13x16)` as a sibling stage *and* left its time
inside the `phase unwrapping (SNAPHU)` row, double-counting it. Both
logs show the nesting directly:

```
journal (crossmul.run):  -- successfully ran crossmul in 208.990 seconds   <- RIFG crossmul (sibling)
journal (unwrap.run):    -- Starting phase unwrapping                       <- unwrap begins
journal (crossmul.run):  -- starting crossmultipy
journal (crossmul.run):  -- successfully ran crossmul in 184.713 seconds    <- INSIDE unwrap
journal (unwrap.run):    -- Unwrapping with SNAPHU
journal (unwrap.run):    -- Successfully ran phase unwrapping in 1090.137 seconds
```

The consequence is that the old **`phase unwrapping ... 1.32x` was an
artifact**. SNAPHU is CPU-only on both paths, and once the nested
crossmul is separated out it shows parity (1.02x, 2.4 % apart) — which
is what a CPU-only stage should look like. 330 s of the 351.8 s gap in
that row is the nested crossmul, which is genuinely GPU-accelerated at
2.79x.

An earlier working hypothesis — that the unwrap-row gap was a
data-driven SNAPHU convergence difference caused by the GPU/CPU
divergence, warranting a 1.56x -> 1.43x correction to the headline — is
**withdrawn**. No counterfactual correction applies: the end-to-end
**1.56x stands as measured**.

An earlier revision also claimed this was "the only nesting in the
workflow". That was wrong — see §.

§ **Correction (2026-08-13).** The `iono chain` row has been through
two restatements. As first published it read 590.5 / 315.0 (a
transcription error against the timing JSON); the 2026-08-12 revision
restated it as the sum of the chain's component occurrences,
600.5 / 315.1. That sum contained a second containment error, found
when the bracket tracking was mechanized: **the ionosphere chain's
`prepare_insar_hdf5` runs `rdr2geo` and `geo2rdr` inside its own
timer**, so adding all component occurrences counts those two twice.
Both logs show the bracket directly (GPU values shown):

```
journal (ionosphere_phase_correction.run): -- starting insar_ionosphere_correction
journal (prepare_insar_hdf5.run): -- preparing InSAR HDF5 products
journal (rdr2geo.run):   -- starting rdr2geo
journal (rdr2geo.run):   -- successfully ran rdr2geo in 40.329 seconds
journal (geo2rdr.run):   -- starting geo2rdr
journal (geo2rdr.run):   -- Successfully ran geo2rdr in 10.363 seconds
journal (prepare_insar_hdf5.run): -- successfully ran prepare_insar_hdf5 in 89.225 seconds
```

Mechanism: the L1 product writer regenerates the geometric offsets
when `geo2rdr/freq*/{range,azimuth}.off` are missing from the scratch
dir (`nisar/products/insar/InSAR_L1_writer.py`), and the ionosphere
chain hits that path because its `rdr2geo` symlink into the main
scratch is created only *after* `prepare_insar_hdf5.run`
(`nisar/workflows/ionosphere.py`). The main chain's occurrence #1 does
not, because the real `geo2rdr` stage has just written the offsets.

Two consequences:

- **`prepare_insar_hdf5 #2`'s apparent 2.13x is an artifact.** Minus
  the nested (GPU-accelerated) `rdr2geo`/`geo2rdr`, prepare proper is
  38.4 s CPU vs 38.5 s GPU — **1.00x parity**, consistent with
  occurrence #1's 0.99x. See "GPU headroom" below.
- **The corrected chain total is 449.0 / 264.5 (1.70x)** — the sum of
  each row's *self* time. In the table above, the chain's direct
  children sum to the parent row; `rdr2geo #2`/`geo2rdr #2` sit inside
  `prepare_insar_hdf5 #2` and must not be added to the chain again.

A related timer subtlety, flagged automatically by the parser: the
`Ionosphere` completion line brackets the whole sub-chain in the log,
but its `t_all` timer starts only after the sub-chain has run
(`nisar/workflows/ionosphere.py`), so its reported 92.7 / 65.8 s
already *excludes* the children — it is the iono estimation + filter
proper, listed as such in the table. (The 2026-08-12 revision claimed
`Ionosphere` "cannot be bracketed from the log"; its start marker is
`starting insar_ionosphere_correction`.)

**Accounting closure (restated 2026-08-13).** Summing every row's self
time (children counted once, inside their parent; `Ionosphere` counted
as its exclusive timer) against the workflow-reported `INSAR` total:

- **CPU**: 6115.2 vs 6175.7 -> **60.5 s unattributed (0.98 %)**
- **GPU**: 3904.3 vs 3959.7 -> **55.4 s unattributed (1.40 %)**

This also resolves the closure anomaly reported on 2026-08-12
(CPU siblings *exceeding* the total by 90.9 s, "unresolved"): the
excess was exactly the iono-chain double count (+151.5 s CPU) minus
the true glue time, and the GPU side's suspiciously clean 4.7 s was
the same two errors nearly cancelling (+50.7 − 55.4). After the
correction both runs land in the same place: ~1 % of wall spent
between stage timers (imports, scratch cleanup, inter-stage
transitions), symmetric across CPU and GPU. No open accounting items
remain.

(Full table: `artifacts/insar-timing-20260810/`;
`tools/parse_insar_timing.py` regenerates table, tree JSON and closure
from any pair of logs — it tracks the `starting X` /
`successfully ran Y` brackets per journal channel, so the nesting
above no longer needs to be applied by hand.)

## What the corrected table implies for GPU headroom

Stages CUDA cannot reach, taken from the same run (`gunw_gpu_run2`, so
no cross-run substitution; restated 2026-08-13 with the
`prepare_insar_hdf5 #2` self time, which the § correction exposed as
parity):

```
prepare_insar_hdf5 #1          562.5   (0.99x - not accelerated)
prepare_insar_hdf5 #2 proper    38.5   (1.00x - iono chain, minus nested rdr2geo/geo2rdr)
polyfit rubbersheet            407.6   (CPU-only)
SNAPHU proper                  905.4   (CPU-only, combinatorial)
phase unwrapping #2             41.7   (CPU-only, iono chain)
solid earth tides               13.0
baseline                        30.3
                              -------
                              1999.1 s  = 50.5 % of the GPU wall
```

Amdahl ceiling = 6175.7 / 1999.1 = **3.09x**, against 1.56x measured.
(Substituting run #3's best-case rubbersheet 224.4 s gives a 1815.9 s
floor and a 3.40x ceiling; that mixes values across runs and is quoted
only as a sensitivity.)

**The ceiling is not GPU headroom.** The two largest remaining items —
fine resample (87 % I/O, below) and `prepare_insar_hdf5` — are bounded
by I/O and HDF5 behaviour, not by arithmetic a CUDA kernel could take
over. On this workload the GPU acceleration story is largely complete
at ~1.4-1.6x; further gains look like I/O and HDF5 engineering.

`prepare_insar_hdf5` remains the strongest next target, but the
question changed on 2026-08-13. As published, the puzzle was the
inconsistency between its two occurrences; the § correction dissolves
it:

| occurrence | CPU | GPU-5080 | CPU/GPU |
|---|---|---|---|
| #1 (freq A) | 557.4 | 562.5 | **0.99x** |
| #2 (freq B), as reported | 189.8 | 89.2 | *2.13x (artifact)* |
| #2 proper (minus nested rdr2geo/geo2rdr) | 38.4 | 38.5 | **1.00x** |

The 2.13x was entirely the nested, genuinely GPU-accelerated
`rdr2geo`/`geo2rdr`; prepare proper sits at parity in both
occurrences, as a pure writer stage should. **No profiling was needed
to explain the asymmetry — the log already contained the answer.**

What remains is occurrence #1's absolute cost: 562.5 s of parity-bound
product-skeleton writing, the single largest non-GPU-addressable
interval in the GPU run (14 % of wall). Occurrence #2 proper prepares
skeletons for frequency B's much smaller rasters in 38 s, so the cost
tracks raster volume — consistent with a per-pixel cost such as a
compression filter or fill-value writes at allocation, not with
per-dataset metadata overhead. `h5dump -pH` on the existing outputs
settles half of that at zero cost.

## Fine resample is I/O-bound (issue #8 analogue)

`resample_slc_v2` logs its own I/O split. Fine resample, CPU run:
497.9 s = 373.2 s I/O (75 %) + 118.6 s compute. GPU run: 442.4 s =
386.6 s I/O (**87 %**) + 52.9 s compute. The CUDA kernel does its job
(2.2x on the compute slice) but the stage is bounded by scattered
HDF5/ENVI reads, which is why the stage speedup is only ~1.05-1.13x.
Same shape as the `geocode_slc` I/O hypothesis
(isce3-benchmark#8). VRAM peak over the whole GPU run: 7.2 GiB of
16 GiB (ASF production knobs; no OOM risk on consumer hardware for
this workload).

## A100 reference leg

A100-SXM4-80GB pod, same v0.25.16 built for sm_80+sm_90. GPU-stage
times (data/scratch on tmpfs): rdr2geo 72.4 s (**3.7x vs RTX 5080**,
FP64-throughput bound), geo2rdr 55.6 s, coarse resample 224.5 s,
dense_offsets 198.7 s. CPU-side stages are not comparable (different
host CPU, network-fs reference RSLC). The full pipeline does not fit
the default pod (117 GB tmpfs + 20 GB volume quota; scratch peaks
~118 GB+) — rerun planned on a 150 GB volume.

**Cross-hardware determinism (sm_80 vs sm_120): NOT bit-identical.**
SHA256 of the geo2rdr azimuth/range offsets differs between the A100
and RTX 5080 runs (identical inputs; per-arch determinism separately
established by three bit-identical RTX 5080 runs). This is expected
CUDA behavior (arch-specific codegen, FMA scheduling, FP64 iteration
convergence). The **magnitude** of the arch-to-arch difference is not
yet quantified (the pod tmpfs was lost on shutdown) — first task for
the 150 GB pod rerun. Until then the hardware-independence of the
CPU-vs-GPU divergence is supported by its sheer size (5 orders above
the CPU noise floor) but not yet by a direct arch-to-arch delta.

## Scope decisions

- H100 leg cancelled (Syota, 2026-08-10): with GPU-vs-CPU equivalence
  broken, generation-scaling numbers add nothing to the upstream
  case. A100 kept as reference + cross-hardware determinism check.
- Next steps: (a) upstream issue draft from findings 1-4;
  (b) stage-level localization runs (intermediates kept);
  (c) optional nsys pass on the surviving suspects after (b).

## Reproduction

- GPU runconfig: `configs/insar_gunw_ASC139_019_20260705_20260717_gpu.yaml`
  (in the `nisar-displacement` checkout)
- Run command: see `.claude-notes/2026-08-10-gpu-equivalence.md`
  (local notes in the `nisar-displacement` checkout; compose `dev`
  service, `ISCE3_SRC`/`ISCE3_BUILD_DIR` -> v0.25.16 worktree/build,
  NVMe-backed `/out` + `/scratch`).
- Comparisons: `scripts/compare_gunw.py A.h5 B.h5 --json out.json`
  (`nisar-displacement`)
- Timing tables: `tools/parse_insar_timing.py cpu.log gpu.log
  --labels CPU GPU` (this repo; gzipped logs in
  `artifacts/insar-timing-20260810/`)
