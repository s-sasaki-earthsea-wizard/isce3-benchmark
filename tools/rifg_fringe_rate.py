#!/usr/bin/env python3
"""Fringe-rate test of the SLC phase convention on a flatten-on / flatten-off RIFG pair.

bench#54 Acceptance 4: the two crossmul variants are formed on the same
registered SLCs and looks; "which one looks nicer" or "which has higher
coherence" does not identify the phase convention (the flatten phasor is
applied per pixel before multilooking, so coherence changes with the
within-window gradient). What does identify it is the **fringe frequency**
of the wrapped interferogram compared with the geometric prediction isce3
itself used for flattening: ``geo2rdr/freqA/range.off`` (secondary-minus-
reference slant-range offset in pixels), which crossmul turns into
``exp(-j * 4*pi * dr * off / lambda)`` (cxx/isce3/signal/Crossmul.cpp).

Let f_geo be the local fringe frequency of that geometric phase, f_raw the
measured one on the flatten-off product and f_flat on the flatten-on one:

    conventional SLC, phase = -4*pi*R/lambda : f_raw = +f_geo, f_flat = 0
    opposite sign convention                  : f_raw = -f_geo, f_flat = -2 f_geo
    phase already compensated to the DEM      : f_raw =  0,     f_flat = -f_geo

Local fringe frequency per block: the peak of the block-averaged power
spectrum of the unit-phasor interferogram along range (and along azimuth),
in cycles per RIFG pixel. The naive estimator angle(sum W[i+1] conj W[i]) is
NOT used: at the coherence of this pair (~0.44 at 5 x 3 looks) the speckle
correlation between adjacent multilooked pixels adds a zero-frequency term
that pulls the circular mean towards 0 on the raw product and towards
+f_geo on the flattened one (measured 0.43 f_geo and -0.30 f_geo on
2026-09-24 where the spectral peaks said 0.99 f_geo and 0.00). The
spectral peak separates the fringe line from that hump.

Usage (inside the dev container, scratch mounted)::

    python tools/rifg_fringe_rate.py --flat /out_flat/product.h5 --noflat /out_noflat/product.h5 \
        --range-off /scratch_flat/geo2rdr/freqA/range.off --out summary.json [--png quicklook.png]
"""

from __future__ import annotations

import argparse
import json
import sys

import h5py
import numpy as np

RIFG = "science/LSAR/RIFG"
RSLC_SW = "science/LSAR/RSLC/swaths"


def _find(fid: h5py.File, name: str) -> str:
    hits = []
    fid.visit(lambda p: hits.append(p) if p.endswith("/" + name) else None)
    if not hits:
        raise KeyError(name)
    return hits[0]


def load_rifg(path: str):
    with h5py.File(path, "r") as fid:
        w = fid[_find(fid, "wrappedInterferogram")][()]
        coh = fid[_find(fid, "coherenceMagnitude")][()]
        g = fid[f"{RIFG}/swaths/frequencyA/interferogram"]
        meta = {
            "slant_range_spacing": float(g["slantRangeSpacing"][()]),
            "zero_doppler_time_spacing": float(g["zeroDopplerTimeSpacing"][()]),
            "shape": list(w.shape),
        }
        # looks and wavelength from the processing information / RSLC copy
        try:
            p = fid[f"{RIFG}/metadata/processingInformation/parameters/interferogram/frequencyA"]
            meta["range_looks"] = int(p["numberOfRangeLooks"][()])
            meta["azimuth_looks"] = int(p["numberOfAzimuthLooks"][()])
        except KeyError:
            pass
    return w, coh, meta


def _peak_freq(power: np.ndarray, nfft: int, halfwidth: int = 3) -> float:
    """Circular centroid of the spectrum within +-halfwidth bins of its peak (cycles/pixel)."""
    f = np.fft.fftfreq(nfft)
    i = int(np.argmax(power))
    sel = (np.arange(nfft) - i + nfft // 2) % nfft - nfft // 2
    sel = np.abs(sel) <= halfwidth
    z = np.sum(power[sel] * np.exp(2j * np.pi * f[sel]))
    return float(np.angle(z) / (2 * np.pi))


def block_fringe(w: np.ndarray, b_az: int, b_rg: int, nfft: int = 4096):
    """Per-block dominant fringe frequency (cycles / RIFG pixel) along range and azimuth.

    Range: blocks of b_az rows x b_rg columns, spectrum along the columns,
    power averaged over the rows. Azimuth: blocks of b_rg rows x b_az columns
    (transposed roles), spectrum along the rows.
    Returns (fr, snr_r, fa, snr_a): fr on a (n_az // b_az, n_rg // b_rg) grid,
    fa on (n_az // b_rg, n_rg // b_az); snr = peak / median power (1 = no line).
    """
    u = w / np.maximum(np.abs(w), 1e-30)
    u = np.where(np.isfinite(u), u, 0).astype(np.complex64)

    def run(x, ba, bl):
        na, nl = x.shape
        nb_a, nb_l = na // ba, nl // bl
        f = np.zeros((nb_a, nb_l)); q = np.zeros((nb_a, nb_l))
        for i in range(nb_a):
            for j in range(nb_l):
                blk = x[i * ba:(i + 1) * ba, j * bl:(j + 1) * bl]
                p = (np.abs(np.fft.fft(blk, n=nfft, axis=1)) ** 2).mean(axis=0)
                f[i, j] = _peak_freq(p, nfft)
                q[i, j] = float(p.max() / max(np.median(p), 1e-30))
        return f, q

    fr, qr = run(u, b_az, b_rg)
    fa, qa = run(np.ascontiguousarray(u.T), b_az, b_rg)   # (n_rg, n_az): rows = range
    return fr, qr, fa.T, qa.T                              # fa on (n_az // b_rg, n_rg // b_az)


def block_mean(x: np.ndarray, ba: int, bl: int):
    na, nl = x.shape
    nb_a, nb_l = na // ba, nl // bl
    return x[: nb_a * ba, : nb_l * bl].reshape(nb_a, ba, nb_l, bl).mean(axis=(1, 3))


def multilook(x: np.ndarray, la: int, lr: int):
    na, nr = x.shape
    na, nr = (na // la) * la, (nr // lr) * lr
    return x[:na, :nr].reshape(na // la, la, nr // lr, lr).mean(axis=(1, 3))


def geometric_fringe(range_off_path: str, la: int, lr: int, dr: float, wvl: float, shape):
    """Fringe frequency of the geometric phase on the RIFG grid, plus a validity mask.

    range.off carries a large negative fill value where geo2rdr did not
    converge / outside the image; those pixels are masked, not multilooked.
    """
    import rasterio
    with rasterio.open(range_off_path) as src:
        off = src.read(1).astype(np.float64)
    valid = np.abs(off) < 1e5
    off = np.where(valid, off, np.nan)
    ml = multilook(off, la, lr)[: shape[0], : shape[1]]
    ok = np.isfinite(ml)
    phase = 4.0 * np.pi * dr * ml / wvl                       # radians on the RIFG grid
    fr = np.full_like(phase, np.nan); fa = np.full_like(phase, np.nan)
    fr[:, :-1] = np.diff(phase, axis=1) / (2 * np.pi)
    fr[:, -1] = fr[:, -2]
    fa[:-1, :] = np.diff(phase, axis=0) / (2 * np.pi)
    fa[-1, :] = fa[-2, :]
    fr = np.nan_to_num(fr); fa = np.nan_to_num(fa)
    ok &= np.isfinite(phase)
    return fr, fa, phase, ok


def wrap_cycles(x: np.ndarray) -> np.ndarray:
    """Wrap a fringe frequency into (-0.5, 0.5] cycles/pixel (the measurable range)."""
    return (x + 0.5) % 1.0 - 0.5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--flat", required=True, help="RIFG product with crossmul.flatten=true")
    ap.add_argument("--noflat", required=True, help="RIFG product with crossmul.flatten=false")
    ap.add_argument("--range-off", required=True, help="scratch geo2rdr/freqA/range.off (full-res pixels)")
    ap.add_argument("--rslc", required=True, help="reference RSLC (wavelength, spacings)")
    ap.add_argument("--block", type=int, default=64, help="block rows (range spectra) / columns (azimuth spectra)")
    ap.add_argument("--block-range", type=int, default=512,
                    help="block length along the spectral axis in RIFG pixels (zero-padded FFT to 4096)")
    ap.add_argument("--min-coh", type=float, default=0.3, help="blocks below this mean coherence are ignored")
    ap.add_argument("--out", required=True, help="JSON summary")
    ap.add_argument("--npz", help="per-block arrays")
    ap.add_argument("--png", help="quicklook figure")
    args = ap.parse_args(argv)

    with h5py.File(args.rslc, "r") as fid:
        fc = float(fid[f"{RSLC_SW}/frequencyA/processedCenterFrequency"][()])
        dr = float(fid[f"{RSLC_SW}/frequencyA/slantRangeSpacing"][()])
    wvl = 299792458.0 / fc

    w_flat, coh_flat, m_flat = load_rifg(args.flat)
    w_raw, coh_raw, m_raw = load_rifg(args.noflat)
    assert w_flat.shape == w_raw.shape, (w_flat.shape, w_raw.shape)
    la = m_flat.get("azimuth_looks") or int(round(m_flat["zero_doppler_time_spacing"] /
                                                   (m_flat["zero_doppler_time_spacing"] / 3)))
    lr = m_flat.get("range_looks") or int(round(m_flat["slant_range_spacing"] / dr))
    b = args.block

    b_rg = args.block_range
    fr_raw, qr_raw, fa_raw, qa_raw = block_fringe(w_raw, b, b_rg)
    fr_flat, qr_flat, fa_flat, qa_flat = block_fringe(w_flat, b, b_rg)
    fr_geo_full, fa_geo_full, phase_geo, off_ok = geometric_fringe(
        args.range_off, la, lr, dr, wvl, w_flat.shape)
    fr_geo = block_mean(fr_geo_full, b, b_rg)
    fa_geo = block_mean(fa_geo_full, b_rg, b)          # azimuth blocks: b_rg rows x b columns
    ok_geo_r = block_mean(off_ok.astype(float), b, b_rg) > 0.99
    ok_geo_a = block_mean(off_ok.astype(float), b_rg, b) > 0.99
    c_raw = block_mean(np.nan_to_num(coh_raw), b, b_rg)
    c_flat = block_mean(np.nan_to_num(coh_flat), b, b_rg)
    c_flat_a = block_mean(np.nan_to_num(coh_flat), b_rg, b)
    ok = (c_flat >= args.min_coh) & ok_geo_r
    ok_a = (c_flat_a >= args.min_coh) & ok_geo_a

    def med(x, m=None):
        m = ok if m is None else m
        return float(np.median(x[m])) if m.any() else float("nan")

    def ratio(num, den, m=None):
        m = (ok if m is None else m) & (np.abs(den) > 1e-3)
        return float(np.median(num[m] / den[m])) if m.any() else float("nan")

    # Predictions are wrapped into the measurable band before comparing.
    fr_geo_w, fa_geo_w = wrap_cycles(fr_geo), wrap_cycles(fa_geo)
    summary = {
        "estimator": "block-averaged range/azimuth power spectrum peak of the unit-phasor interferogram",
        "block_px_az_rg": [b, b_rg], "looks_az_rg": [la, lr], "wavelength_m": wvl,
        "rifg_shape": list(w_flat.shape),
        "blocks_used_range": int(ok.sum()), "blocks_total_range": int(ok.size),
        "blocks_used_azimuth": int(ok_a.sum()), "blocks_total_azimuth": int(ok_a.size),
        "coherence_mean": {"flat": float(np.nanmean(coh_flat)), "noflat": float(np.nanmean(coh_raw))},
        "coherence_frac_above_0.5": {"flat": float(np.nanmean(coh_flat > 0.5)),
                                     "noflat": float(np.nanmean(coh_raw > 0.5))},
        "coherence_median_blocks": {"flat": med(c_flat), "noflat": med(c_raw)},
        "range_fringe_cpp": {
            "geo_median": med(fr_geo), "geo_wrapped_median": med(fr_geo_w),
            "raw_median": med(fr_raw), "flat_median": med(fr_flat),
            "raw_over_geo": ratio(fr_raw, fr_geo_w), "flat_over_geo": ratio(fr_flat, fr_geo_w),
            "raw_minus_geo_median": med(wrap_cycles(fr_raw - fr_geo_w)),
            "peak_to_median_power": {"raw": med(qr_raw), "flat": med(qr_flat)},
        },
        "azimuth_fringe_cpp": {
            "geo_median": med(fa_geo, ok_a), "geo_wrapped_median": med(fa_geo_w, ok_a),
            "raw_median": med(fa_raw, ok_a), "flat_median": med(fa_flat, ok_a),
            "raw_over_geo": ratio(fa_raw, fa_geo_w, ok_a), "flat_over_geo": ratio(fa_flat, fa_geo_w, ok_a),
            "raw_minus_geo_median": med(wrap_cycles(fa_raw - fa_geo_w), ok_a),
            "peak_to_median_power": {"raw": med(qa_raw, ok_a), "flat": med(qa_flat, ok_a)},
        },
        "geometric_phase_span_cycles": float(
            (np.nanmax(phase_geo[off_ok]) - np.nanmin(phase_geo[off_ok])) / (2 * np.pi)),
    }
    r = summary["range_fringe_cpp"]["raw_over_geo"]
    f = summary["range_fringe_cpp"]["flat_over_geo"]
    if np.isfinite(r) and abs(r - 1) < 0.15 and abs(f) < 0.15:
        verdict = "CONVENTIONAL: raw fringe rate = geometric prediction, flattened residual ~0"
    elif np.isfinite(r) and abs(r + 1) < 0.15:
        verdict = "OPPOSITE SIGN: raw fringe rate = -geometric; flattening doubles it"
    elif np.isfinite(r) and abs(r) < 0.15 and abs(f + 1) < 0.15:
        verdict = "COMPENSATED: raw interferogram flat, flattening injects the geometric fringes"
    else:
        verdict = "UNDETERMINED: see ratios"
    summary["verdict"] = verdict
    json.dump(summary, open(args.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))

    if args.npz:
        np.savez(args.npz, fr_raw=fr_raw, fa_raw=fa_raw, fr_flat=fr_flat, fa_flat=fa_flat,
                 fr_geo=fr_geo, fa_geo=fa_geo, c_raw=c_raw, c_flat=c_flat, ok=ok, ok_a=ok_a,
                 qr_raw=qr_raw, qr_flat=qr_flat, qa_raw=qa_raw, qa_flat=qa_flat)
    if args.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        s = max(1, w_flat.shape[1] // 800)
        fig, ax = plt.subplots(2, 3, figsize=(16, 9))
        # The raw fringes are ~5 px apart on the RIFG grid, so a decimated
        # full image aliases to noise; show a full-resolution crop instead.
        ca, cr = w_raw.shape[0] // 2, w_raw.shape[1] // 2
        crop = np.s_[ca - 120:ca + 120, cr - 120:cr + 120]
        ax[0, 0].imshow(np.angle(w_raw[crop]), cmap="twilight", aspect="auto")
        ax[0, 0].set_title(f"wrapped phase, flatten=false (240x240 px crop at {ca},{cr})")
        ax[0, 1].imshow(np.angle(w_flat[::s, ::s]), cmap="twilight", aspect="auto"); ax[0, 1].set_title("wrapped phase, flatten=true")
        im = ax[0, 2].imshow(coh_flat[::s, ::s], cmap="gray", vmin=0, vmax=1, aspect="auto"); ax[0, 2].set_title("coherence, flatten=true")
        fig.colorbar(im, ax=ax[0, 2])
        v = max(np.nanmax(np.abs(fr_geo_w)), 1e-3)
        for k, (name, arr) in enumerate((("f_raw (range)", fr_raw), ("f_geo (range, wrapped)", fr_geo_w), ("f_flat (range)", fr_flat))):
            im = ax[1, k].imshow(np.where(ok, arr, np.nan), cmap="RdBu_r", vmin=-v, vmax=v, aspect="auto")
            ax[1, k].set_title(f"{name} [cycles/px]")
            fig.colorbar(im, ax=ax[1, k])
        fig.suptitle(verdict)
        fig.tight_layout()
        fig.savefig(args.png, dpi=80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
