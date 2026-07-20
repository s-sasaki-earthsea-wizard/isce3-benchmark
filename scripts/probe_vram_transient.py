#!/usr/bin/env python3
"""
High-rate VRAM sampler for one CUDA Geocode call.

Why: the in-process 20 ms VramSampler in trial_cuda_geocode_subswath.py
reported peaks far below the sinc chip-buffer model
(n_elem_out * 81 * 8 B device transient — ~30 GB at lines_per_block=2000
on a 23k-wide geogrid, vs ~2.3 GiB sampled). Either the transient is
shorter than the sampling cadence / lost to GIL contention, or the
chip buffer does not materialize the way the model says. This probe
removes both confounders: the sampler is a separate *process* (no GIL)
busy-polling pynvml at sub-ms cadence.

Usage (pod):
  micromamba run -n isce3 python scripts/probe_vram_transient.py \
      --config configs/trial_a100_iw2_subswath.yaml --lpb 2000
"""

import sys

_ARGV = sys.argv[:]
sys.argv = [sys.argv[0]]

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def sampler_main(out_path):
    """Child process: busy-poll device memory, write samples on SIGTERM."""
    import pynvml
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    samples = []
    t0 = time.perf_counter()
    stop = {'flag': False}

    def on_term(signum, frame):
        stop['flag'] = True

    signal.signal(signal.SIGTERM, on_term)
    while not stop['flag']:
        used = pynvml.nvmlDeviceGetMemoryInfo(handle).used
        samples.append((time.perf_counter() - t0, used))
    peak_t, peak = max(samples, key=lambda s: s[1])
    Path(out_path).write_text(json.dumps({
        'n_samples': len(samples),
        'duration_s': round(samples[-1][0], 3),
        'rate_hz': round(len(samples) / samples[-1][0]),
        'baseline_mib': round(min(s[1] for s in samples) / 2**20),
        'peak_mib': round(peak / 2**20),
        'peak_at_s': round(peak_t, 3),
        'over_4gib_s': round(sum(1 for _, u in samples if u > 4 * 2**30)
                             / (len(samples) / samples[-1][0]), 3),
    }))
    pynvml.nvmlShutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--lpb', type=int, required=True)
    parser.add_argument('--work', default='/dev/shm/vram_probe')
    parser.add_argument('--sampler-out', default=None,
                        help='(internal) run as sampler child')
    args = parser.parse_args(_ARGV[1:])

    if args.sampler_out:
        sampler_main(args.sampler_out)
        return

    import numpy as np  # noqa: F401
    import isce3
    from compass.utils.geo_runconfig import GeoRunConfig

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from trial_cuda_geocode_subswath import burst_inputs
    from trial_cuda_geocode_e2e import run_gpu

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    cfg = GeoRunConfig.load_from_yaml(args.config, 's1_cslc_geo')
    burst = sorted(cfg.bursts, key=lambda b: str(b.burst_id))[0]
    inp = burst_inputs(cfg, burst)
    vrt = work / 'probe.vrt'
    inp['burst'].slc_to_vrt_file(str(vrt))
    raster = isce3.io.Raster(str(vrt))

    # warm-up call (CUDA context etc.) before sampling
    run_gpu(work / 'probe_out.slc', raster, inp, args.lpb)

    sampler_json = work / f'sampler_lpb{args.lpb}.json'
    child = subprocess.Popen(
        [sys.executable, __file__, '--config', args.config,
         '--lpb', str(args.lpb), '--sampler-out', str(sampler_json)])
    time.sleep(0.5)  # let the sampler reach steady state
    dt = run_gpu(work / 'probe_out.slc', raster, inp, args.lpb)
    time.sleep(0.2)
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=30)

    result = json.loads(sampler_json.read_text())
    result.update({'lines_per_block': args.lpb, 'geocode_wall_s': round(dt, 3),
                   'geogrid': [inp['geo_grid'].length, inp['geo_grid'].width],
                   'chip_model_gib': round(
                       args.lpb * inp['geo_grid'].width * 81 * 8 / 2**30, 2)})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
