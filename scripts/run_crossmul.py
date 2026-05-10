#!/usr/bin/env python3
"""Direct call to isce3.signal.Crossmul / isce3.cuda.signal.Crossmul.

This is the GPU-primitive-direct stage of the Sentinel-1 InSAR bench. The
COMPASS radar-mode CSLC step produces coregistered radar-grid burst SLCs;
this script cross-multiplies a reference + secondary pair to produce a
wrapped interferogram (ifg) and (optionally) a coherence raster.

Why this script exists: the user's contribution strategy is to land
measurement infrastructure that exposes isce3's GPU InSAR primitives on real
S1 data. NISAR uses these via `nisar.workflows.crossmul` (RSLC HDF5 only).
COMPASS / dolphin do NOT call them. Calling crossmul directly here closes
that loop without any upstream change.

Usage:
  python scripts/run_crossmul.py \\
      --reference  /logs/scratch_.../ref_burst.slc \\
      --secondary  /logs/scratch_.../sec_burst_resampled.slc \\
      --out-ifg    /logs/scratch_.../ifg.int \\
      --out-coh    /logs/scratch_.../coh.bin \\
      --range-looks 4 --azimuth-looks 1 \\
      --gpu                              # add to dispatch to CUDA path
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--config", type=Path,
                     help="YAML runconfig (runconfig.name == crossmul_s1)")
    src.add_argument("--reference", type=Path,
                     help="reference radar-grid SLC raster (cf32)")
    p.add_argument("--secondary", type=Path,
                   help="secondary radar-grid SLC raster, coregistered to reference")
    p.add_argument("--out-ifg", type=Path,
                   help="output wrapped interferogram raster")
    p.add_argument("--out-coh", default=None, type=Path,
                   help="optional output coherence raster")
    p.add_argument("--range-looks", type=int, default=4)
    p.add_argument("--azimuth-looks", type=int, default=1)
    p.add_argument("--oversample", type=int, default=2,
                   help="range oversample factor for crossmul (default: 2)")
    p.add_argument("--gpu", action="store_true",
                   help="dispatch to isce3.cuda.signal.Crossmul instead of CPU")
    p.add_argument("--gpu-id", type=int, default=0)
    return p.parse_args()


def _load_from_yaml(cfg_path: Path) -> argparse.Namespace:
    """Map runconfig YAML into the same Namespace shape as CLI flags."""
    import yaml
    d = yaml.safe_load(cfg_path.read_text())
    g = d["runconfig"]["groups"]
    inp = g["input"]
    out = g["output"]
    proc = g.get("processing") or {}
    worker = g.get("worker") or {}
    ns = argparse.Namespace(
        config=cfg_path,
        reference=Path(inp["reference_slc"]),
        secondary=Path(inp["secondary_slc"]),
        out_ifg=Path(out["interferogram"]),
        out_coh=Path(out["coherence"]) if out.get("coherence") else None,
        range_looks=int(proc.get("range_looks", 4)),
        azimuth_looks=int(proc.get("azimuth_looks", 1)),
        oversample=int(proc.get("oversample", 2)),
        gpu=bool(worker.get("gpu_enabled", False)),
        gpu_id=int(worker.get("gpu_id", 0)),
    )
    return ns


def main() -> int:
    args = _parse_args()
    if args.config is not None:
        args = _load_from_yaml(args.config)
    elif args.reference is None or args.secondary is None or args.out_ifg is None:
        print("[crossmul] error: --reference, --secondary, --out-ifg required without --config", file=sys.stderr)
        return 2
    import isce3

    for f in (args.reference, args.secondary):
        if not f.exists():
            print(f"[crossmul] error: input not found: {f}", file=sys.stderr)
            return 2
    args.out_ifg.parent.mkdir(parents=True, exist_ok=True)

    if args.gpu:
        # NOTE: dispatching to isce3.cuda.signal.Crossmul requires the
        # from-source isce3 build with CUDA enabled. The conda-forge isce3
        # is CPU-only, so this attribute will be absent there. The
        # entrypoint.sh PYTHONPATH precedence ensures we hit the GPU build.
        if not hasattr(isce3, "cuda") or not hasattr(isce3.cuda, "signal"):
            print("[crossmul] error: isce3.cuda.signal not available — "
                  "the active isce3 install was built without CUDA. "
                  "Confirm /opt/isce3-build/install/packages is on PYTHONPATH.",
                  file=sys.stderr)
            return 3
        crossmul = isce3.cuda.signal.Crossmul()
        backend = "isce3.cuda.signal.Crossmul"
    else:
        crossmul = isce3.signal.Crossmul()
        backend = "isce3.signal.Crossmul"

    crossmul.range_looks = args.range_looks
    crossmul.az_looks = args.azimuth_looks
    crossmul.oversample = args.oversample

    ref_raster = isce3.io.Raster(str(args.reference))
    sec_raster = isce3.io.Raster(str(args.secondary))

    cols = ref_raster.width  // args.range_looks
    rows = ref_raster.length // args.azimuth_looks
    ifg_raster = isce3.io.Raster(str(args.out_ifg), cols, rows, 1, "CFloat32", "ENVI")
    coh_raster = None
    if args.out_coh is not None:
        coh_raster = isce3.io.Raster(str(args.out_coh), cols, rows, 1, "Float32", "ENVI")

    print(f"[crossmul] backend={backend} range_looks={args.range_looks} "
          f"az_looks={args.azimuth_looks} oversample={args.oversample}")
    print(f"[crossmul] ref={args.reference} ({ref_raster.width}x{ref_raster.length})")
    print(f"[crossmul] sec={args.secondary}")
    print(f"[crossmul] ifg={args.out_ifg} ({cols}x{rows})")

    t0 = time.perf_counter()
    if coh_raster is not None:
        crossmul.crossmul(ref_raster, sec_raster, ifg_raster, coh_raster)
    else:
        crossmul.crossmul(ref_raster, sec_raster, ifg_raster)
    elapsed = time.perf_counter() - t0
    print(f"[crossmul] done in {elapsed:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
