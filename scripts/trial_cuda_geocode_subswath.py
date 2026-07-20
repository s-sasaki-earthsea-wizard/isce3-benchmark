#!/usr/bin/env python3
"""
Full-subswath scale-up of the CUDA Geocode vs geocode_slc trial
(scripts/trial_cuda_geocode_e2e.py) — designed for the Runpod A100
session feeding the second upstream RFC.

Same three variants and validation stages as the single-burst trial,
applied per burst across every burst in the runconfig (e.g. all 9 IW2
VV bursts), plus what only a multi-burst run can measure:

  batch pass      One process sequentially geocoding every burst on the
                  GPU, cold start included on burst 1. Per-burst walls +
                  total wall = the amortization evidence (how much of
                  the single-burst cold cost survives at subswath
                  scale).
  lpb scan        lines_per_block scan on one representative burst with
                  per-run pynvml VRAM peak sampling. On 80 GB the sinc
                  chip-buffer transient stops being the binding
                  constraint — this feeds the adaptive-block-sizing
                  discussion in the RFC.

Timed-region semantics are identical to the single-burst trial (CPU =
array-mode pure compute; GPU = raster read + H2D + kernels + D2H +
raster API writes inside the timer). Scratch should live on tmpfs
(/dev/shm) on the pod — the 20 GB container overlay is too small for
9 bursts of cf32 geogrid rasters.

Run (pod):
  scripts/run_trial_subswath_a100.sh configs/trial_a100_iw2_subswath.yaml
"""

# Stash argv before importing isce3: pyre's journal reads sys.argv
# eagerly and chokes on unknown flags (see scripts/run_crossmul.py).
import sys

_ARGV = sys.argv[:]
sys.argv = [sys.argv[0]]

import argparse
import json
import threading
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

import isce3
from compass.utils.geo_runconfig import GeoRunConfig

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trial_cuda_geocode_e2e import (  # noqa: E402
    CF32_NAN,
    invariance_metrics,
    make_coord_raster,
    median_iqr,
    read_cf32,
    run_cpu,
    run_gpu,
    stage1_geometry_metrics,
    stage2_amplitude_metrics,
)

gdal.UseExceptions()


def burst_inputs(cfg, burst):
    """Per-burst input dict with the same keys run_cpu/run_gpu consume
    (mirrors trial_cuda_geocode_e2e.load_inputs, minus the config
    reload)."""
    burst_id = str(burst.burst_id)
    dem_path = cfg.dem
    epsg = isce3.io.Raster(dem_path).get_epsg()
    ellipsoid = isce3.core.make_projection(epsg).ellipsoid
    inp = {
        'burst': burst,
        'burst_id': burst_id,
        'geo_grid': cfg.geogrids[burst_id],
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
    inp['sliced_radar_grid'] = burst.as_isce3_radargrid()[b_bounds]
    return inp


class VramSampler:
    """Poll pynvml for used device memory in a thread; keep the peak.
    50 ms proved necessary to catch the short-lived sinc chip transient
    (200 ms sampling missed it entirely on the RTX 5080 run)."""

    def __init__(self, interval_s=0.02, device_index=0):
        import pynvml
        self._nvml = pynvml
        self._interval = interval_s
        self._index = device_index
        self._peak = 0
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._nvml.nvmlInit()
        self._handle = self._nvml.nvmlDeviceGetHandleByIndex(self._index)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            mem = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
            self._peak = max(self._peak, mem.used)
            self._stop.wait(self._interval)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self._nvml.nvmlShutdown()

    @property
    def peak_mib(self):
        return round(self._peak / (1024 * 1024))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--work', default='/dev/shm/trial_subswath')
    parser.add_argument('--repeats', type=int, default=3,
                        help='per-burst warm repeats per variant')
    parser.add_argument('--lines-per-block', type=int, default=200,
                        help='GPU block size for the main passes (kept at '
                             'the RTX-safe 200 for cross-host comparability)')
    parser.add_argument('--lpb-scan', default='100,200,500,1000,2000',
                        help='comma-separated lines_per_block values for the '
                             'scan on the scan burst ("" disables)')
    parser.add_argument('--scan-burst-index', type=int, default=0)
    parser.add_argument('--skip-cpu', action='store_true',
                        help='GPU-only run (for reruns/scans)')
    args = parser.parse_args(_ARGV[1:])

    run_dir = Path(args.run_dir)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    print(f'# loading inputs from {args.config}')
    cfg = GeoRunConfig.load_from_yaml(args.config, 's1_cslc_geo')
    bursts = sorted(cfg.bursts, key=lambda b: str(b.burst_id))
    inputs = [burst_inputs(cfg, b) for b in bursts]
    print(f'# {len(inputs)} burst(s): {[i["burst_id"] for i in inputs]}')

    results = {'config': args.config,
               'n_bursts': len(inputs),
               'burst_ids': [i['burst_id'] for i in inputs],
               'repeats': args.repeats,
               'gpu_lines_per_block': args.lines_per_block,
               'geo_grids': {i['burst_id']: [i['geo_grid'].length,
                                             i['geo_grid'].width]
                             for i in inputs}}

    # Stage SLC arrays/rasters once per burst (VRT into the SAFE; the
    # SAFE should sit on fast storage — reads happen inside GPU timers).
    slc_raster, slc_array = {}, {}
    for inp in inputs:
        bid = inp['burst_id']
        vrt = work / f'{bid}.vrt'
        inp['burst'].slc_to_vrt_file(str(vrt))
        slc_raster[bid] = isce3.io.Raster(str(vrt))
        slc_array[bid] = np.ascontiguousarray(read_cf32(vrt))

    # --- GPU batch pass: sequential sweep, cold start included -------------
    print('# gpu batch pass (cold start on burst 1, then sequential)')
    batch = {'per_burst_s': {}, 'order': [i['burst_id'] for i in inputs]}
    t0 = time.perf_counter()
    for inp in inputs:
        bid = inp['burst_id']
        dt = run_gpu(work / f'gpu_{bid}.slc', slc_raster[bid], inp,
                     args.lines_per_block)
        batch['per_burst_s'][bid] = round(dt, 3)
        print(f'  batch {bid}: {dt:.2f} s')
    batch['total_wall_s'] = round(time.perf_counter() - t0, 3)
    results['gpu_batch'] = batch

    # --- per-burst warm repeats -------------------------------------------
    timings = {}
    cpu_parity_out = {}
    for inp in inputs:
        bid = inp['burst_id']
        entry = {}
        reps = []
        for i in range(args.repeats):
            dt = run_gpu(work / f'gpu_{bid}.slc', slc_raster[bid], inp,
                         args.lines_per_block)
            reps.append(dt)
        entry['gpu'] = median_iqr(reps)
        if not args.skip_cpu:
            for name, parity in [('cpu_parity', True), ('cpu_prod', False)]:
                reps = []
                for i in range(args.repeats):
                    dt, arr = run_cpu(slc_array[bid], inp, parity=parity)
                    reps.append(dt)
                    if parity:
                        cpu_parity_out[bid] = arr
                entry[name] = median_iqr(reps)
        timings[bid] = entry
        msg = ', '.join(f'{k} {v["median_s"]:.2f}s' for k, v in entry.items())
        print(f'  {bid}: {msg}')
    results['timings'] = timings

    # --- aggregates --------------------------------------------------------
    agg = {'gpu_sum_of_medians_s':
           round(sum(t['gpu']['median_s'] for t in timings.values()), 3),
           'gpu_batch_total_wall_s': batch['total_wall_s']}
    if not args.skip_cpu:
        for name in ('cpu_parity', 'cpu_prod'):
            agg[f'{name}_sum_of_medians_s'] = round(
                sum(t[name]['median_s'] for t in timings.values()), 3)
        agg['speedup_parity_sum'] = round(
            agg['cpu_parity_sum_of_medians_s'] / agg['gpu_sum_of_medians_s'], 2)
        agg['speedup_parity_vs_batch_wall'] = round(
            agg['cpu_parity_sum_of_medians_s'] / agg['gpu_batch_total_wall_s'], 2)
    results['aggregate'] = agg
    print(f'# aggregate: {json.dumps(agg)}')

    # --- validation per burst (stage 1 geometry + stage 2 amplitude) -------
    if not args.skip_cpu:
        print('# validation per burst')
        validation = {}
        for inp in inputs:
            bid = inp['burst_id']
            coord_path = work / f'coord_{bid}.slc'
            coord_raster = make_coord_raster(coord_path, inp['radar_grid'])
            coord_array = np.ascontiguousarray(read_cf32(coord_path))
            _, cpu_coord = run_cpu(coord_array, inp, parity=True)
            run_gpu(work / f'gpu_coord_{bid}.slc', coord_raster, inp,
                    args.lines_per_block)
            v = {'stage1_geometry': stage1_geometry_metrics(
                    cpu_coord, read_cf32(work / f'gpu_coord_{bid}.slc')),
                 'stage2_amplitude': stage2_amplitude_metrics(
                    cpu_parity_out[bid], read_cf32(work / f'gpu_{bid}.slc'))}
            validation[bid] = v
            s1 = v['stage1_geometry']
            print(f'  {bid}: jaccard={s1["mask_jaccard"]:.6f} '
                  f'rg_max={s1["rg_index_max_abs_diff"]} '
                  f'az_max={s1["az_index_max_abs_diff"]} '
                  f'amp_r={v["stage2_amplitude"]["amplitude_pearson_r"]:.6f}')
        results['validation'] = validation

    # --- lines_per_block scan with VRAM peaks ------------------------------
    if args.lpb_scan:
        scan_inp = inputs[args.scan_burst_index]
        bid = scan_inp['burst_id']
        print(f'# lines_per_block scan on {bid}')
        scan = []
        for lpb in [int(x) for x in args.lpb_scan.split(',')]:
            entry = {'lines_per_block': lpb}
            try:
                walls = []
                peak = None
                for rep in range(2):  # rep 0 warms the size class
                    with VramSampler() as vs:
                        dt = run_gpu(work / f'gpu_scan_{bid}.slc',
                                     slc_raster[bid], scan_inp, lpb)
                    walls.append(round(dt, 3))
                    peak = vs.peak_mib
                entry['wall_s'] = walls[-1]
                entry['walls_s'] = walls
                entry['vram_peak_mib_sampled'] = peak
                # cross-check against the main-pass output for correctness
                inv = invariance_metrics(
                    read_cf32(work / f'gpu_{bid}.slc'),
                    read_cf32(work / f'gpu_scan_{bid}.slc'))
                entry['vs_main_lpb'] = {
                    'n_differing_px': inv['n_differing_px'],
                    'differing_ppm': inv['differing_ppm'],
                    'max_abs_diff': inv['max_abs_diff']}
            except Exception as e:  # OOM etc. — record, keep scanning
                entry['error'] = f'{type(e).__name__}: {e}'
            scan.append(entry)
            print(f'  lpb={lpb}: {json.dumps(entry)}')
        results['lpb_scan'] = {'burst_id': bid, 'runs': scan}

    out_json = run_dir / 'results.json'
    out_json.write_text(json.dumps(results, indent=2))
    print(f'# wrote {out_json}')


if __name__ == '__main__':
    main()
