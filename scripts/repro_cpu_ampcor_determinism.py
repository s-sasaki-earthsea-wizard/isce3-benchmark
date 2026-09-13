#!/usr/bin/env python3
"""Run-to-run reproducibility check for CPU Ampcor (isce3.matchtemplate.PyCPUAmpcor).

Self-contained and synthetic — no granules. A band-limited complex noise
field is the reference; the secondary is the same field shifted by a known
sub-pixel amount (exact Fourier shift) plus a little independent noise.
PyCPUAmpcor is run N times in *separate processes* on the byte-identical
inputs and its five output rasters are hashed.

Checks:
  accuracy (always)            median recovered offset == known shift within
                               --tolerance-px, on >= 90 % of windows
  --expect-identical           all N runs agree byte-for-byte on every layer
  --expect-wisdom-enforcement  with wisdom pinning built in, a bogus wisdom
                               path makes the run fail explicitly instead of
                               silently re-measuring

No golden hashes are stored: runs are only compared with each other, so
the check does not depend on the host toolchain. A very quiet host can make
stock FFTW_MEASURE converge on one plan, so a pass of --expect-identical on
stock code is possible; the check is a property test for a fixed planner
policy, not a proof that unseeded MEASURE always differs.

Window geometry is the NISAR dense_offsets production configuration
(window 64x96, half-search 32/32, SLC oversampling 2, batch 10x1,
frequency-domain correlation, FFT surface oversampling 16), which
exercises all seven FFTW planner sites (12 planner calls per process);
only the number of windows is reduced (default 10x10, seconds per run).

Sign convention of the output: dense_offsets = position of the reference
window's content in the secondary minus its position in the reference,
i.e. a feature moved by +s reads as +s. Band 1 = azimuth (down), band 2 =
range (across), pixel-interleaved float32, as rubbersheet.py reads them.

usage (inside the isce3 container):
  python3 repro_cpu_ampcor_determinism.py --workdir /tmp/det --runs 5
  python3 repro_cpu_ampcor_determinism.py --workdir /tmp/det --runs 5 \\
      --wisdom-export /tmp/det/wisdom.f --wisdom-import /tmp/det/wisdom.f \\
      --expect-identical --expect-wisdom-enforcement
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# --- production dense_offsets geometry (NISAR GUNW runconfig) ---------------
WINDOW_W, WINDOW_H = 64, 96          # window_range, window_azimuth
HALF_SEARCH = 32                     # half_search_range / _azimuth
SKIP = 75                            # skip_range / skip_azimuth
RAW_OVERSAMPLING = 2                 # slc_oversampling_factor
CORR_STAT_WINDOW = 21                # correlation_statistics_zoom
CORR_ZOOM_WINDOW = 8                 # correlation_surface_zoom
CORR_SURFACE_OVERSAMPLING = 16       # correlation_surface_oversampling_factor
CHUNK_ACROSS, CHUNK_DOWN = 10, 1     # windows_batch_range / _azimuth
LAYERS = ("dense_offsets", "gross_offset", "snr", "covariance", "correlation_peak")
BANDS = {"dense_offsets": 2, "gross_offset": 2, "snr": 1, "covariance": 3,
         "correlation_peak": 1}


def image_size(n_windows):
    width = HALF_SEARCH + SKIP * (n_windows - 1) + WINDOW_W + HALF_SEARCH + 64
    height = HALF_SEARCH + SKIP * (n_windows - 1) + WINDOW_H + HALF_SEARCH + 64
    return width, height


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_envi_cfloat32(path, arr):
    from osgeo import gdal
    drv = gdal.GetDriverByName("ENVI")
    ds = drv.Create(str(path), arr.shape[1], arr.shape[0], 1, gdal.GDT_CFloat32,
                    options=["INTERLEAVE=BIP"])
    ds.GetRasterBand(1).WriteArray(arr)
    ds.FlushCache()
    ds = None


def create_empty_dataset(path, width, length, bands):
    from osgeo import gdal
    drv = gdal.GetDriverByName("ENVI")
    ds = drv.Create(str(path), width, length, bands, gdal.GDT_Float32,
                    options=["INTERLEAVE=BIP"])
    ds = None


def make_inputs(workdir, n_windows, seed, shift_az, shift_rg, noise):
    """Band-limited complex noise reference + exactly shifted secondary."""
    width, height = image_size(n_windows)
    rng = np.random.default_rng(seed)
    spec = rng.standard_normal((height, width)) + 1j * rng.standard_normal((height, width))
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.fftfreq(width)[None, :]
    band = (np.abs(fy) < 0.4) & (np.abs(fx) < 0.4)      # 80 % of Nyquist
    spec *= band
    ref = np.fft.ifft2(spec)
    ref /= np.sqrt(np.mean(np.abs(ref) ** 2))
    # g(x) = f(x - s)  <=>  G(k) = F(k) exp(-2 pi i k s): content moves by +s
    ramp = np.exp(-2j * np.pi * (fy * shift_az + fx * shift_rg))
    sec = np.fft.ifft2(spec * ramp)
    sec /= np.sqrt(np.mean(np.abs(sec) ** 2))
    nspec = (rng.standard_normal((height, width)) + 1j * rng.standard_normal((height, width))) * band
    nz = np.fft.ifft2(nspec)
    nz /= np.sqrt(np.mean(np.abs(nz) ** 2))
    sec = sec + noise * nz
    write_envi_cfloat32(workdir / "reference.slc", ref.astype(np.complex64))
    write_envi_cfloat32(workdir / "secondary.slc", sec.astype(np.complex64))
    meta = {
        "width": width, "height": height, "n_windows": n_windows, "seed": seed,
        "shift_az": shift_az, "shift_rg": shift_rg, "noise": noise,
        "reference_sha256": sha256(workdir / "reference.slc"),
        "secondary_sha256": sha256(workdir / "secondary.slc"),
    }
    (workdir / "inputs.json").write_text(json.dumps(meta, indent=1))
    return meta


def run_worker(workdir, outdir):
    """One PyCPUAmpcor run — a fresh process each time (see main)."""
    import isce3  # noqa: E402  (argv already sanitised in main)
    meta = json.loads((workdir / "inputs.json").read_text())
    n = meta["n_windows"]
    outdir.mkdir(parents=True, exist_ok=True)

    a = isce3.matchtemplate.PyCPUAmpcor()
    a.useMmap = 1
    a.referenceImageName = str(workdir / "reference.slc")
    a.referenceImageHeight = meta["height"]
    a.referenceImageWidth = meta["width"]
    a.secondaryImageName = str(workdir / "secondary.slc")
    a.secondaryImageHeight = meta["height"]
    a.secondaryImageWidth = meta["width"]

    a.windowSizeWidth = WINDOW_W
    a.windowSizeHeight = WINDOW_H
    a.halfSearchRangeAcross = HALF_SEARCH
    a.halfSearchRangeDown = HALF_SEARCH
    a.skipSampleAcross = SKIP
    a.skipSampleDown = SKIP
    a.referenceStartPixelAcrossStatic = HALF_SEARCH
    a.referenceStartPixelDownStatic = HALF_SEARCH
    a.numberWindowAcross = n
    a.numberWindowDown = n
    a.algorithm = 0                          # frequency domain -> cuFreqCorrelator
    a.rawDataOversamplingFactor = RAW_OVERSAMPLING
    a.derampMethod = 1                       # complex
    a.derampAxis = 0                         # azimuth
    a.corrStatWindowSize = CORR_STAT_WINDOW
    a.corrSurfaceZoomInWindow = CORR_ZOOM_WINDOW
    a.corrSurfaceOverSamplingFactor = CORR_SURFACE_OVERSAMPLING
    a.corrSurfaceOverSamplingMethod = 0      # fft -> cuOverSamplerR2R
    a.numberWindowAcrossInChunk = min(CHUNK_ACROSS, n)
    a.numberWindowDownInChunk = CHUNK_DOWN
    a.setupParams()
    a.setConstantGrossOffset(0, 0)
    a.checkPixelInImageRange()

    a.offsetImageName = str(outdir / "dense_offsets")
    a.grossOffsetImageName = str(outdir / "gross_offset")
    a.snrImageName = str(outdir / "snr")
    a.covImageName = str(outdir / "covariance")
    a.corrImageName = str(outdir / "correlation_peak")
    for layer in LAYERS:
        create_empty_dataset(outdir / layer, n, n, BANDS[layer])

    t0 = time.perf_counter()
    a.runAmpcor()
    wall = time.perf_counter() - t0
    (outdir / "run.json").write_text(json.dumps({"wall_s": wall}))


def spawn(script, workdir, outdir, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    env.setdefault("PYCUAMPCOR_FFTW_LOG", "1")     # harmless if the build ignores it
    cmd = [sys.executable, script, "--worker", str(outdir), "--workdir", str(workdir)]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    planner_lines = [l for l in p.stderr.splitlines() if "[pycuampcor-fftw]" in l]
    return p.returncode, planner_lines, p.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/cpu_ampcor_determinism"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--windows", type=int, default=10, help="windows per axis (grid is N x N)")
    ap.add_argument("--seed", type=int, default=20260913)
    ap.add_argument("--shift-az", type=float, default=-0.375, help="known azimuth shift, px")
    ap.add_argument("--shift-rg", type=float, default=0.25, help="known range shift, px")
    ap.add_argument("--noise", type=float, default=0.05, help="secondary noise amplitude (RMS ratio)")
    ap.add_argument("--tolerance-px", type=float, default=0.0625, help="2 x the 1/32 px quantum")
    ap.add_argument("--wisdom-export", type=Path, help="generator run: export FFTW wisdom here first")
    ap.add_argument("--wisdom-import", type=Path, help="run the N replicates with this wisdom pinned")
    ap.add_argument("--expect-identical", action="store_true")
    ap.add_argument("--expect-wisdom-enforcement", action="store_true")
    ap.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    # pyre reads sys.argv eagerly on `import isce3`; strip our flags first.
    sys.argv = sys.argv[:1]

    if args.worker is not None:
        run_worker(args.workdir, args.worker)
        return 0

    script = os.path.abspath(__file__)
    wd = args.workdir
    wd.mkdir(parents=True, exist_ok=True)
    meta = make_inputs(wd, args.windows, args.seed, args.shift_az, args.shift_rg, args.noise)
    print(f"inputs: {meta['width']}x{meta['height']} px, {args.windows}x{args.windows} windows, "
          f"known shift az={args.shift_az:+.4f} rg={args.shift_rg:+.4f} px")
    print(f"  reference sha256 {meta['reference_sha256'][:16]}  secondary sha256 {meta['secondary_sha256'][:16]}")
    failures = []

    if args.wisdom_export:
        rc, lines, err = spawn(script, wd, wd / "run_wisdom_gen", {"PYCUAMPCOR_FFTW_WISDOM_EXPORT": str(args.wisdom_export)})
        ok = rc == 0 and args.wisdom_export.exists() and args.wisdom_export.stat().st_size > 0
        print(f"wisdom generator run: rc={rc} planner_calls={len(lines)} "
              f"wisdom={'%d B sha256 %s' % (args.wisdom_export.stat().st_size, sha256(args.wisdom_export)[:16]) if ok else 'MISSING'}")
        if not ok:
            failures.append("wisdom export produced no file")

    if args.expect_wisdom_enforcement:
        rc, lines, err = spawn(script, wd, wd / "run_bogus_wisdom", {"PYCUAMPCOR_FFTW_WISDOM_IMPORT": str(wd / "does_not_exist.wisdom")})
        enforced = rc != 0
        print(f"wisdom enforcement: bogus wisdom path -> rc={rc} -> {'FAIL CLOSED (ok)' if enforced else 'ran anyway (NOT enforced)'}")
        if not enforced:
            failures.append("missing wisdom did not fail explicitly")

    env_runs = {"PYCUAMPCOR_FFTW_WISDOM_IMPORT": str(args.wisdom_import)} if args.wisdom_import else {}
    hashes = {layer: [] for layer in LAYERS}
    walls, calls = [], []
    for i in range(1, args.runs + 1):
        out = wd / f"run_{i}"
        rc, lines, err = spawn(script, wd, out, env_runs)
        if rc != 0:
            print(f"run {i}: rc={rc}\n{err[-2000:]}")
            failures.append(f"run {i} failed")
            continue
        walls.append(json.loads((out / "run.json").read_text())["wall_s"])
        calls.append(len(lines))
        for layer in LAYERS:
            hashes[layer].append(sha256(out / layer))
    print(f"replicates: {len(walls)}/{args.runs} ok, wall {min(walls):.2f}-{max(walls):.2f} s, "
          f"planner calls/run {sorted(set(calls))} (0 = build has no probe)")

    print("layer              distinct/runs")
    identical = True
    for layer in LAYERS:
        d = len(set(hashes[layer]))
        print(f"  {layer:<17} {d}/{len(hashes[layer])}{'' if d <= 1 else '   <- differs'}")
        identical &= d <= 1
    if args.expect_identical and not identical:
        failures.append("outputs differ between runs")

    # accuracy on run 1 (all runs share it when identical)
    n = args.windows
    off = np.fromfile(wd / "run_1" / "dense_offsets", dtype=np.float32).reshape(n, n, 2)
    az, rg = off[..., 0], off[..., 1]
    err_az, err_rg = az - args.shift_az, rg - args.shift_rg
    within = (np.abs(err_az) <= args.tolerance_px) & (np.abs(err_rg) <= args.tolerance_px)
    snr = np.fromfile(wd / "run_1" / "snr", dtype=np.float32)
    peak = np.fromfile(wd / "run_1" / "correlation_peak", dtype=np.float32)
    print(f"accuracy (run 1): median offset az={np.median(az):+.4f} rg={np.median(rg):+.4f} px "
          f"(known {args.shift_az:+.4f}/{args.shift_rg:+.4f}); max |err| az={np.abs(err_az).max():.4f} "
          f"rg={np.abs(err_rg).max():.4f}; {within.mean()*100:.0f}% of windows within {args.tolerance_px} px; "
          f"median snr {np.median(snr):.2f}, median peak {np.median(peak):.3f}")
    if abs(np.median(az) - args.shift_az) > args.tolerance_px or abs(np.median(rg) - args.shift_rg) > args.tolerance_px \
            or within.mean() < 0.9:
        failures.append("recovered shift outside tolerance")

    print("RESULT:", "PASS" if not failures else "FAIL: " + "; ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
