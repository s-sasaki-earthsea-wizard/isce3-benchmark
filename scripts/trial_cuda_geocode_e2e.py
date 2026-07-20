#!/usr/bin/env python3
"""
End-to-end trial of the existing isce3.cuda.geocode.Geocode on a real
Sentinel-1 burst, against the CPU isce3.geocode.geocode_slc.

Purpose (team round-4 consensus): infrastructure-performance and
geometry-equivalence evidence for the "extend existing CUDA Geocode"
RFC framing. This is NOT a geocode_slc correctness test — the CUDA
class lacks carrier/doppler/flatten semantics (see the static gap diff
in the 2026-07-20 session notes), so full-semantics parity is
impossible by construction.

Three measured variants, all on the same burst / geogrid / DEM:

  cpu_prod    isce3.geocode.geocode_slc with COMPASS-equivalent
              semantics (flatten, reramp, az carrier, native doppler,
              sliced radar grid). Anchors to the known production
              kernel wall time.
  cpu_parity  isce3.geocode.geocode_slc with carrier/doppler/flatten
              neutralized (zero Poly2d carriers, default LUT2d native
              doppler, no flatten/reramp, full radar grid). The
              apples-to-apples partner for the CUDA class.
  gpu         isce3.cuda.geocode.Geocode.geocode_rasters, SINC
              interpolation, same geo2rdr params as the CPU calls.

Validation:

  Stage 1 (geometry): geocode a synthetic cf32 raster that encodes its
      own radar coordinates (real = range index, imag = azimuth index)
      through cpu_parity and gpu. The outputs ARE each side's geo2rdr
      index maps as seen through the actual code paths under test.
      Metrics: max/RMS index difference on the common valid mask, and
      valid-mask agreement (Jaccard).
  Stage 2 (pixel values): amplitude correlation between gpu and
      cpu_parity outputs of the real SLC (amplitude is carrier-
      invariant), plus relative amplitude error stats.
  Block-size invariance: gpu outputs at lines_per_block and
      lines_per_block/2 must match (max abs diff on common-finite).

Timing: n repeats per variant, median + IQR. GPU cold (first) call is
recorded separately from warm repeats. No HDF5 / compression anywhere
in the timed path. Timed-region asymmetry (deliberate — each side is
timed the way its production caller pays for it): CPU uses the
array-mode API exactly like COMPASS (input SLC preloaded, output
in-memory, pure compute inside the timer); GPU geocode_rasters reads
the input raster, transfers, computes, and writes the output raster
inside the timer (GDAL I/O on container-local disk, not the NAS).

Run inside the dev container, e.g.:
  docker compose run --rm dev bash scripts/run_trial_cuda_geocode.sh
"""

# Stash argv before importing isce3: pyre's journal reads sys.argv
# eagerly and chokes on unknown flags (see scripts/run_crossmul.py).
import sys

_ARGV = sys.argv[:]
sys.argv = [sys.argv[0]]

import argparse
import json
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

import isce3
from compass.utils.geo_runconfig import GeoRunConfig

gdal.UseExceptions()

CF32_NAN = complex(np.nan, np.nan)


def load_inputs(cfg_path):
    """Load burst/grid/geometry inputs exactly as compass.s1_geocode_slc
    does (bursts_grouping_generator collapses to the first burst here —
    the Boso config has a single VV burst)."""
    cfg = GeoRunConfig.load_from_yaml(cfg_path, 's1_cslc_geo')
    burst = cfg.bursts[0]
    burst_id = str(burst.burst_id)
    geo_grid = cfg.geogrids[burst_id]

    dem_path = cfg.dem
    epsg = isce3.io.Raster(dem_path).get_epsg()
    ellipsoid = isce3.core.make_projection(epsg).ellipsoid

    inputs = {
        'cfg': cfg,
        'burst': burst,
        'burst_id': burst_id,
        'geo_grid': geo_grid,
        'dem_path': dem_path,
        'ellipsoid': ellipsoid,
        'radar_grid': burst.as_isce3_radargrid(),
        'orbit': burst.orbit,
        'native_doppler': burst.doppler.lut2d,
        'az_carrier': burst.get_az_carrier_poly(),
        'threshold': cfg.geo2rdr_params.threshold,
        'numiter': cfg.geo2rdr_params.numiter,
        'flatten': cfg.geocoding_params.flatten,
    }
    b_bounds = np.s_[burst.first_valid_line:burst.last_valid_line,
                     burst.first_valid_sample:burst.last_valid_sample]
    inputs['sliced_radar_grid'] = burst.as_isce3_radargrid()[b_bounds]
    return inputs


def make_output_raster(path, geo_grid):
    return isce3.io.Raster(str(path), geo_grid.width, geo_grid.length, 1,
                           gdal.GDT_CFloat32, 'ENVI')


def make_coord_raster(path, radar_grid):
    """cf32 raster whose value at (az, rg) is complex(rg, az). Geocoding
    it yields the geocoder's own index maps (float32 is exact for
    integers up to 2^24; burst dims are far below that)."""
    length, width = radar_grid.length, radar_grid.width
    rg = np.arange(width, dtype=np.float32)[np.newaxis, :]
    az = np.arange(length, dtype=np.float32)[:, np.newaxis]
    data = (np.broadcast_to(rg, (length, width))
            + 1j * np.broadcast_to(az, (length, width))).astype(np.complex64)
    drv = gdal.GetDriverByName('ENVI')
    ds = drv.Create(str(path), width, length, 1, gdal.GDT_CFloat32)
    ds.GetRasterBand(1).WriteArray(data)
    ds.FlushCache()
    del ds
    return isce3.io.Raster(str(path))


def run_cpu(rdr_array, inp, parity):
    """One CPU geocode_slc call via the public array-mode API — the same
    call shape COMPASS s1_geocode_slc uses (input array preloaded,
    output array in memory; the timer wraps pure geocode compute).
    parity=True neutralizes carrier / doppler / flatten and uses the
    full radar grid. Returns (seconds, output array)."""
    out_shape = (inp['geo_grid'].length, inp['geo_grid'].width)
    geo_block = np.full(out_shape, CF32_NAN, dtype=np.complex64)
    dem_raster = isce3.io.Raster(inp['dem_path'])  # fresh: modified by callee
    zero_poly = isce3.core.Poly2d(np.array([0.0]))
    common = dict(
        geo_data_blocks=geo_block,
        rdr_data_blocks=rdr_array,
        dem_raster=dem_raster,
        radargrid=inp['radar_grid'],
        geogrid=inp['geo_grid'],
        orbit=inp['orbit'],
        image_grid_doppler=isce3.core.LUT2d(),
        ellipsoid=inp['ellipsoid'],
        threshold_geo2rdr=inp['threshold'],
        num_iter_geo2rdr=inp['numiter'],
        first_azimuth_line=0,
        first_range_sample=0,
        rg_carrier=zero_poly,
        invalid_value=CF32_NAN,
    )
    t0 = time.perf_counter()
    if parity:
        isce3.geocode.geocode_slc(
            native_doppler=isce3.core.LUT2d(),
            flatten=False, reramp=False,
            az_carrier=zero_poly,
            **common)
    else:
        isce3.geocode.geocode_slc(
            sliced_radargrid=inp['sliced_radar_grid'],
            native_doppler=inp['native_doppler'],
            flatten=inp['flatten'], reramp=True,
            az_carrier=inp['az_carrier'],
            **common)
    dt = time.perf_counter() - t0
    return dt, geo_block


def run_gpu(out_path, in_raster, inp, lines_per_block):
    """One CUDA Geocode.geocode_rasters call (construction included in
    the timed region: block sizing is a constructor argument, so a
    production caller pays it too)."""
    out_raster = make_output_raster(out_path, inp['geo_grid'])
    dem_raster = isce3.io.Raster(inp['dem_path'])
    rdr_geom = isce3.container.RadarGeometry(
        inp['radar_grid'], inp['orbit'], isce3.core.LUT2d())
    t0 = time.perf_counter()
    geocode_obj = isce3.cuda.geocode.Geocode(
        inp['geo_grid'], rdr_geom, lines_per_block)
    geocode_obj.geocode_rasters(
        [out_raster], [in_raster],
        [isce3.core.DataInterpMethod.SINC],
        [isce3.io.gdal.GDT_CFloat32],
        [np.float64('nan')],
        dem_raster,
        native_doppler=isce3.core.LUT2d(),
        threshold=inp['threshold'],
        maxiter=inp['numiter'],
        delta_range=1.0e-8)
    dt = time.perf_counter() - t0
    del out_raster
    return dt


def read_cf32(path):
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    arr = ds.GetRasterBand(1).ReadAsArray()
    del ds
    return arr


def stage1_geometry_metrics(cpu_arr, gpu_arr):
    """Index-map comparison. real = range index, imag = azimuth index."""
    cpu_valid = np.isfinite(cpu_arr)
    gpu_valid = np.isfinite(gpu_arr)
    both = cpu_valid & gpu_valid
    either = cpu_valid | gpu_valid
    n_both = int(both.sum())
    d_rg = np.abs(cpu_arr.real[both] - gpu_arr.real[both])
    d_az = np.abs(cpu_arr.imag[both] - gpu_arr.imag[both])
    return {
        'n_valid_cpu': int(cpu_valid.sum()),
        'n_valid_gpu': int(gpu_valid.sum()),
        'n_valid_both': n_both,
        'mask_jaccard': n_both / int(either.sum()) if either.any() else 1.0,
        'rg_index_max_abs_diff': float(d_rg.max()) if n_both else None,
        'rg_index_rms_diff': float(np.sqrt(np.mean(d_rg ** 2))) if n_both else None,
        'az_index_max_abs_diff': float(d_az.max()) if n_both else None,
        'az_index_rms_diff': float(np.sqrt(np.mean(d_az ** 2))) if n_both else None,
        'rg_index_p99_abs_diff': float(np.percentile(d_rg, 99)) if n_both else None,
        'az_index_p99_abs_diff': float(np.percentile(d_az, 99)) if n_both else None,
    }


def stage2_amplitude_metrics(cpu_arr, gpu_arr):
    both = np.isfinite(cpu_arr) & np.isfinite(gpu_arr)
    a_cpu = np.abs(cpu_arr[both]).astype(np.float64)
    a_gpu = np.abs(gpu_arr[both]).astype(np.float64)
    nz = a_cpu > 0
    rel = np.abs(a_gpu[nz] - a_cpu[nz]) / a_cpu[nz]
    return {
        'n_valid_both': int(both.sum()),
        'amplitude_pearson_r': float(np.corrcoef(a_cpu, a_gpu)[0, 1]),
        'rel_amp_err_mean': float(rel.mean()),
        'rel_amp_err_p99': float(np.percentile(rel, 99)),
        'rel_amp_err_max': float(rel.max()),
    }


def invariance_metrics(arr_a, arr_b):
    """Elementwise comparison of two runs. Reviewers will ask 'is it
    deterministic?' — quantify how many pixels differ and where."""
    fin_a, fin_b = np.isfinite(arr_a), np.isfinite(arr_b)
    both = fin_a & fin_b
    d = np.abs(arr_a - arr_b)
    d[~both] = 0.0
    differing = d > 0
    n_both = int(both.sum())
    n_diff = int(differing.sum())
    diff_rows = np.flatnonzero(differing.any(axis=1))
    return {
        'finite_mask_mismatch': int((fin_a != fin_b).sum()),
        'n_valid_both': n_both,
        'n_differing_px': n_diff,
        'differing_ppm': round(1e6 * n_diff / n_both, 3) if n_both else None,
        'max_abs_diff': float(d.max()) if n_both else None,
        'rms_abs_diff': float(np.sqrt(np.mean(d[both] ** 2))) if n_both else None,
        'p99_abs_diff': float(np.percentile(d[both], 99)) if n_both else None,
        'n_rows_with_diffs': int(diff_rows.size),
        'diff_row_range': ([int(diff_rows.min()), int(diff_rows.max())]
                           if diff_rows.size else None),
    }


def median_iqr(vals):
    v = np.asarray(vals)
    return {'n': len(vals), 'median_s': float(np.median(v)),
            'iqr_s': float(np.percentile(v, 75) - np.percentile(v, 25)),
            'all_s': [round(float(x), 3) for x in vals]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--run-dir', required=True,
                        help='directory for metrics/timings artifacts')
    parser.add_argument('--work', default='/tmp/trial_cuda_geocode',
                        help='scratch for large rasters (container-local '
                             'disk, NOT the NAS-backed /logs)')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--lines-per-block', type=int, default=200,
                        help='GPU block size. Sinc chip buffer is '
                             'n_elem*81*8B: keep <=200 for this geogrid '
                             'on 16 GiB VRAM')
    args = parser.parse_args(_ARGV[1:])

    run_dir = Path(args.run_dir)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    print(f'# loading inputs from {args.config}')
    inp = load_inputs(args.config)
    rg_grid = inp['radar_grid']
    print(f'# burst {inp["burst_id"]}: radar {rg_grid.length}x{rg_grid.width}, '
          f'geogrid {inp["geo_grid"].length}x{inp["geo_grid"].width}, '
          f'geo2rdr threshold={inp["threshold"]} numiter={inp["numiter"]}')

    # Real burst SLC: raster for the GPU path (reads inside the call),
    # preloaded array for the CPU array-mode path (COMPASS-identical).
    slc_vrt = work / 'burst_vv.vrt'
    inp['burst'].slc_to_vrt_file(str(slc_vrt))
    slc_raster = isce3.io.Raster(str(slc_vrt))
    slc_array = np.ascontiguousarray(read_cf32(slc_vrt))
    cpu_out = {}

    results = {'config': args.config, 'burst_id': inp['burst_id'],
               'radar_grid': [rg_grid.length, rg_grid.width],
               'geo_grid': [inp['geo_grid'].length, inp['geo_grid'].width],
               'repeats': args.repeats,
               'gpu_lines_per_block': args.lines_per_block}

    # --- timing runs -------------------------------------------------------
    timings = {}
    print('# gpu cold run (CUDA context + first-touch)')
    cold = run_gpu(work / 'gpu_par.slc', slc_raster, inp, args.lines_per_block)
    timings['gpu_cold_first_call_s'] = round(cold, 3)
    print(f'  gpu cold: {cold:.2f} s')

    def gpu_rep():
        return run_gpu(work / 'gpu_par.slc', slc_raster, inp,
                       args.lines_per_block), None

    def cpu_parity_rep():
        return run_cpu(slc_array, inp, parity=True)

    def cpu_prod_rep():
        return run_cpu(slc_array, inp, parity=False)

    for name, fn in [('gpu', gpu_rep),
                     ('cpu_parity', cpu_parity_rep),
                     ('cpu_prod', cpu_prod_rep)]:
        reps = []
        for i in range(args.repeats):
            dt, arr = fn()
            reps.append(dt)
            if arr is not None:
                cpu_out[name] = arr  # keep last rep's output for metrics
            print(f'  {name} rep {i + 1}/{args.repeats}: {dt:.2f} s')
        timings[name] = median_iqr(reps)
    results['timings'] = timings

    # --- determinism: same block size, two runs ----------------------------
    print('# gpu determinism run (same lines_per_block)')
    run_gpu(work / 'gpu_par_rerun.slc', slc_raster, inp, args.lines_per_block)
    det = invariance_metrics(read_cf32(work / 'gpu_par.slc'),
                             read_cf32(work / 'gpu_par_rerun.slc'))
    results['gpu_determinism_same_block'] = det

    # --- block-size invariance (GPU, half block size) ----------------------
    print('# gpu block-size invariance run')
    half = max(args.lines_per_block // 2, 1)
    run_gpu(work / 'gpu_par_half.slc', slc_raster, inp, half)
    inv = invariance_metrics(read_cf32(work / 'gpu_par.slc'),
                             read_cf32(work / 'gpu_par_half.slc'))
    inv['lines_per_block_pair'] = [args.lines_per_block, half]
    results['gpu_block_invariance'] = inv

    # --- stage 1: geometry via coordinate-encoded raster -------------------
    print('# stage 1: geometry (coordinate-encoded raster)')
    coord_raster = make_coord_raster(work / 'coord.slc', rg_grid)
    coord_array = np.ascontiguousarray(read_cf32(work / 'coord.slc'))
    _, cpu_coord = run_cpu(coord_array, inp, parity=True)
    run_gpu(work / 'gpu_coord.slc', coord_raster, inp, args.lines_per_block)
    results['stage1_geometry'] = stage1_geometry_metrics(
        cpu_coord, read_cf32(work / 'gpu_coord.slc'))

    # Coordinate-raster block invariance: bisects whether the block-size
    # sensitivity of the real-SLC output comes from the geometry stage
    # (per-block DEM interpolator) or the interpolation stage.
    run_gpu(work / 'gpu_coord_half.slc', coord_raster, inp, half)
    cinv = invariance_metrics(read_cf32(work / 'gpu_coord.slc'),
                              read_cf32(work / 'gpu_coord_half.slc'))
    cinv['lines_per_block_pair'] = [args.lines_per_block, half]
    results['gpu_coord_block_invariance'] = cinv

    # --- stage 2: amplitude on the real SLC --------------------------------
    print('# stage 2: amplitude comparison')
    results['stage2_amplitude'] = stage2_amplitude_metrics(
        cpu_out['cpu_parity'], read_cf32(work / 'gpu_par.slc'))

    out_json = run_dir / 'results.json'
    out_json.write_text(json.dumps(results, indent=2))
    print(f'# wrote {out_json}')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
