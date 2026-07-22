#!/usr/bin/env python3
"""Sanity-compare a locally produced NISAR GCOV against the official granule.

Supports both output layouts produced by ``nisar.workflows.gcov``:

- HDF5 mode (default runconfig): covariance terms inside
  ``science/LSAR/GCOV/grids/frequency{F}/{TERM}`` of the output .h5
- GTiff mode (``output.output_gcov_terms.format: GTiff``): single-band
  ``frequency{F}_{TERM}.tif`` files next to the output .h5

The reference is always the official GCOV HDF5. Comparison is
coordinate-aware (pixel-center alignment check + overlap window) and streams
row blocks so freqA-scale grids (34k x 35k) stay within memory. Statistics
are computed in the dB domain over pixels valid in both products.

Usage:
    python scripts/compare_gcov_nisar.py --ours <gcov.h5 | output-dir> \
        --ref <official_gcov.h5> --freq A [--terms HHHH HVHV]

Exit code 0 always (this is a measurement, not a gate); parse stdout.
"""

import argparse
import os
import sys

import h5py
import numpy as np

BLOCK_ROWS = 2048


def _ref_grid(ref, freq):
    g = ref[f'science/LSAR/GCOV/grids/frequency{freq}']
    return g, g['xCoordinates'][()], g['yCoordinates'][()]


class _H5Term:
    """Our-product term backed by the output HDF5 grids group."""

    def __init__(self, h5, freq, term):
        g = h5[f'science/LSAR/GCOV/grids/frequency{freq}']
        self.data = g[term]
        self.x = g['xCoordinates'][()]
        self.y = g['yCoordinates'][()]

    def read(self, row0, row1, col0, ncols):
        return self.data[row0:row1, col0:col0 + ncols]


class _TifTerm:
    """Our-product term backed by a single-band GTiff (GTiff output mode)."""

    def __init__(self, path):
        from osgeo import gdal
        self.ds = gdal.Open(path)
        if self.ds is None:
            raise FileNotFoundError(path)
        gt = self.ds.GetGeoTransform()
        nx, ny = self.ds.RasterXSize, self.ds.RasterYSize
        # pixel-center coordinates
        self.x = gt[0] + gt[1] * (np.arange(nx) + 0.5)
        self.y = gt[3] + gt[5] * (np.arange(ny) + 0.5)
        self.band = self.ds.GetRasterBand(1)

    def read(self, row0, row1, col0, ncols):
        return self.band.ReadAsArray(int(col0), int(row0), int(ncols),
                                     int(row1 - row0))


def _open_ours(ours, freq, term):
    if os.path.isdir(ours):
        return _TifTerm(os.path.join(ours, f'frequency{freq}_{term}.tif'))
    h5 = h5py.File(ours, 'r')
    return _H5Term(h5, freq, term)


def compare_term(ours_term, ref_ds, xr, yr, label):
    xo, yo = ours_term.x, ours_term.y
    dx, dy = xo[1] - xo[0], yo[1] - yo[0]
    off_x = (xo[0] - xr[0]) / dx
    off_y = (yo[0] - yr[0]) / dy
    print(f"{label}: ours {xo.size}x{yo.size} @ ({dx:g},{dy:g}) | "
          f"ref {xr.size}x{yr.size} | grid offset ({off_x:.6f}, {off_y:.6f}) px"
          f"{'' if abs(off_x - round(off_x)) < 1e-6 else '  ** NOT pixel-aligned **'}")

    x0, x1 = max(xo.min(), xr.min()), min(xo.max(), xr.max())
    y0, y1 = max(yo.min(), yr.min()), min(yo.max(), yr.max())
    oxi = np.where((xo >= x0 - 1) & (xo <= x1 + 1))[0]
    oyi = np.where((yo >= y0 - 1) & (yo <= y1 + 1))[0]
    rxi = np.where((xr >= x0 - 1) & (xr <= x1 + 1))[0]
    ryi = np.where((yr >= y0 - 1) & (yr <= y1 + 1))[0]
    n_rows = min(oyi.size, ryi.size)
    n_cols = min(oxi.size, rxi.size)

    s = dict(n=0, a=0.0, b=0.0, aa=0.0, bb=0.0, ab=0.0, d=0.0, dd=0.0,
             fin_o=0, fin_r=0, tot=0)
    for r0 in range(0, n_rows, BLOCK_ROWS):
        r1 = min(r0 + BLOCK_ROWS, n_rows)
        A = ours_term.read(oyi[0] + r0, oyi[0] + r1, oxi[0], n_cols)
        R = ref_ds[ryi[0] + r0:ryi[0] + r1, rxi[0]:rxi[0] + n_cols]
        fa, fb = np.isfinite(A), np.isfinite(R)
        s['tot'] += A.size
        s['fin_o'] += int(fa.sum())
        s['fin_r'] += int(fb.sum())
        m = fa & fb & (A > 0) & (R > 0)
        if not m.any():
            continue
        da, db_ = 10 * np.log10(A[m]), 10 * np.log10(R[m])
        d = da - db_
        s['n'] += d.size
        s['a'] += da.sum()
        s['b'] += db_.sum()
        s['aa'] += float((da * da).sum())
        s['bb'] += float((db_ * db_).sum())
        s['ab'] += float((da * db_).sum())
        s['d'] += d.sum()
        s['dd'] += float((d * d).sum())

    if s['n'] == 0:
        print(f"{label}: no common valid pixels in overlap")
        return
    n = s['n']
    mu_a, mu_b = s['a'] / n, s['b'] / n
    var_a = s['aa'] / n - mu_a ** 2
    var_b = s['bb'] / n - mu_b ** 2
    cov = s['ab'] / n - mu_a * mu_b
    mu_d = s['d'] / n
    sd_d = max(s['dd'] / n - mu_d ** 2, 0.0) ** 0.5
    print(f"{label}: overlap {n_rows}x{n_cols} "
          f"finite ours={s['fin_o']/s['tot']:.4f} ref={s['fin_r']/s['tot']:.4f} "
          f"common={n/s['tot']:.4f}")
    print(f"{label}:   ours dB mean={mu_a:+.3f} sd={var_a**0.5:.3f} | "
          f"ref dB mean={mu_b:+.3f} sd={var_b**0.5:.3f}")
    print(f"{label}:   diff dB mean={mu_d:+.4f} sd={sd_d:.4f} | "
          f"corr={cov/(var_a*var_b)**0.5:.6f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ours', required=True,
                    help='our GCOV .h5, or the output dir holding '
                         'frequency{F}_{TERM}.tif (GTiff mode)')
    ap.add_argument('--ref', required=True, help='official GCOV .h5')
    ap.add_argument('--freq', required=True, choices=['A', 'B'])
    ap.add_argument('--terms', nargs='+', default=['HHHH', 'HVHV'])
    args = ap.parse_args()

    with h5py.File(args.ref, 'r') as ref:
        g, xr, yr = _ref_grid(ref, args.freq)
        for term in args.terms:
            ours = _open_ours(args.ours, args.freq, term)
            compare_term(ours, g[term], xr, yr, f'freq{args.freq}/{term}')


if __name__ == '__main__':
    main()
