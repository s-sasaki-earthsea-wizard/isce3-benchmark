#!/usr/bin/env python3
"""Render the Sentinel-1 CSLC runconfig templates to concrete YAMLs.

Substitutes @PLACEHOLDER@ tokens in
``configs/insar_s1_boso_cslc_{cpu,gpu}.yaml.template`` and writes
``configs/insar_s1_boso_cslc_{cpu,gpu}.yaml`` (the reference run) and
``configs/insar_s1_boso_cslc_sec_{cpu,gpu}.yaml`` (the secondary run, with
``is_reference: False`` and ``file_path`` pointing at the reference product).

Usage:
  python tools/render_s1_runconfig.py \\
      --bursts data/S1-boso/bursts.json \\
      --orbits-dir data/S1-boso/orbits \\
      --dem data/S1-boso/dem.tif \\
      --burst-id t073_153512_iw2 \\
      --pol VV

If ``--burst-id`` is omitted, the script picks the first burst that appears
in BOTH SAFE files (i.e. the first burst suitable for an InSAR pair).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = REPO_ROOT / "configs"

# Container-side bind-mount roots (must match docker-compose.yml).
DATA_MOUNT = "/data"
LOGS_MOUNT = "/logs"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bursts", required=True, type=Path,
                   help="bursts.json produced by fetch/fetch_sentinel1.py")
    p.add_argument("--orbits-dir", required=True, type=Path,
                   help="directory containing POEORB EOF files")
    p.add_argument("--dem", required=True, type=Path,
                   help="DEM tiff path")
    p.add_argument("--burst-id", default=None,
                   help="burst id to use (default: first overlapping in both SAFEs)")
    p.add_argument("--pol", default="VV",
                   help="polarization (default: VV)")
    p.add_argument("--data-host-root", type=Path, default=REPO_ROOT / "data",
                   help="host root that maps to /data inside container")
    return p.parse_args()


def _to_container_path(host_path: Path, host_root: Path, mount: str) -> str:
    """Translate a host path under host_root to the container's bind mount."""
    host_path = host_path.resolve()
    host_root = host_root.resolve()
    rel = host_path.relative_to(host_root)
    return f"{mount}/{rel.as_posix()}"


def _pick_overlap_burst(records: list[dict], pol: str) -> tuple[str, dict[str, str]]:
    """Find a burst_id present in both SAFE files for the given polarization.

    Returns (burst_id, {safe_basename: <eof_or_safe_filename_per_safe_basename>}).
    """
    by_burst: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in records:
        if r["polarization"] != pol:
            continue
        by_burst[r["burst_id"]][r["safe"]] = r

    safes = sorted({r["safe"] for r in records})
    if len(safes) != 2:
        raise SystemExit(f"expected exactly 2 SAFE files in bursts.json, got {len(safes)}: {safes}")

    for burst_id in sorted(by_burst):
        if set(by_burst[burst_id]) == set(safes):
            return burst_id, {s: by_burst[burst_id][s] for s in safes}
    raise SystemExit(f"no burst overlapping in both SAFEs for pol={pol}")


def _match_orbit(safe_basename: str, orbits_dir: Path) -> Path:
    """Pick the EOF whose start/end window straddles the SAFE's sensing time.

    sentineleof produces filenames like
    S1A_OPER_AUX_POEORB_OPOD_<creation>_V<start>_<end>.EOF.
    SAFE basename is S1A_IW_SLC__1SDV_<sensing_start>_<sensing_end>_..._.SAFE.
    We just match by mission letter + sensing-start-falls-within-EOF-window.
    """
    sensing_start = safe_basename.split("_")[5]  # e.g. 20251221T204341
    mission = safe_basename[:3]                  # S1A or S1B

    for eof in sorted(orbits_dir.glob(f"{mission}_OPER_AUX_POEORB_*.EOF")):
        parts = eof.stem.split("_")
        v_start = parts[6][1:]  # strip leading "V"
        v_end = parts[7]
        if v_start <= sensing_start <= v_end:
            return eof
    raise SystemExit(f"no POEORB EOF found in {orbits_dir} covering {safe_basename} sensing_start={sensing_start}")


def _substitute(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, val in mapping.items():
        out = out.replace(f"@{key}@", val)
    return out


def main() -> int:
    args = _parse_args()
    records = json.loads(args.bursts.read_text())

    if args.burst_id:
        # validate that the chosen burst exists in both SAFEs
        safes = sorted({r["safe"] for r in records if r["burst_id"] == args.burst_id and r["polarization"] == args.pol})
        if len(safes) != 2:
            raise SystemExit(f"burst {args.burst_id} pol={args.pol} not in both SAFEs (found in {safes})")
        burst_id = args.burst_id
    else:
        burst_id, _ = _pick_overlap_burst(records, args.pol)
        print(f"[render] auto-selected overlapping burst: {burst_id}")

    safe_basenames = sorted({r["safe"] for r in records})
    safe_ref, safe_sec = safe_basenames[0], safe_basenames[1]

    # data/S1-data/<safe>.SAFE — locate by basename under data_host_root
    safe_ref_host = args.data_host_root / "S1-data" / safe_ref
    safe_sec_host = args.data_host_root / "S1-data" / safe_sec
    for s in (safe_ref_host, safe_sec_host):
        if not s.is_dir():
            raise SystemExit(f"SAFE dir not found on host: {s}")

    orbit_ref_host = _match_orbit(safe_ref, args.orbits_dir)
    orbit_sec_host = _match_orbit(safe_sec, args.orbits_dir)

    # All host paths under data/ are translated to /data/...
    safe_ref_c = _to_container_path(safe_ref_host, args.data_host_root, DATA_MOUNT)
    safe_sec_c = _to_container_path(safe_sec_host, args.data_host_root, DATA_MOUNT)
    orbit_ref_c = _to_container_path(orbit_ref_host, args.data_host_root, DATA_MOUNT)
    orbit_sec_c = _to_container_path(orbit_sec_host, args.data_host_root, DATA_MOUNT)
    dem_c = _to_container_path(args.dem, args.data_host_root, DATA_MOUNT)

    # Reference runconfig (CPU + GPU)
    for path in ("cpu", "gpu"):
        tpl = (CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml.template").read_text()
        product = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/product"
        scratch = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/scratch"
        rendered = _substitute(tpl, {
            "SAFE_REF": safe_ref_c,
            "ORBIT_REF": orbit_ref_c,
            "DEM": dem_c,
            "BURST_ID": burst_id,
            "PRODUCT_PATH": product,
            "SCRATCH_PATH": scratch,
        })
        out = CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml"
        out.write_text(rendered)
        print(f"[render] wrote {out} (reference burst, {path})")

    # Secondary runconfig — coregistered to reference
    for path in ("cpu", "gpu"):
        tpl = (CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml.template").read_text()
        # rewrite: SAFE_REF -> SAFE_SEC, ORBIT_REF -> ORBIT_SEC,
        # is_reference -> False, file_path -> reference product path
        product = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/product_sec"
        scratch = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/scratch_sec"
        ref_product = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/product"
        rendered = _substitute(tpl, {
            "SAFE_REF": safe_sec_c,
            "ORBIT_REF": orbit_sec_c,
            "DEM": dem_c,
            "BURST_ID": burst_id,
            "PRODUCT_PATH": product,
            "SCRATCH_PATH": scratch,
        })
        rendered = rendered.replace(
            "is_reference: True\n                file_path:",
            f"is_reference: False\n                file_path: {ref_product}",
        )
        out = CFG_DIR / f"insar_s1_boso_cslc_sec_{path}.yaml"
        out.write_text(rendered)
        print(f"[render] wrote {out} (secondary burst, {path})")

    print(f"[render] burst_id={burst_id} pol={args.pol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
