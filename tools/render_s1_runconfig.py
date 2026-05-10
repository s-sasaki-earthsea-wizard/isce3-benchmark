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
                   help="directory containing POEORB EOF files (container-side path)")
    p.add_argument("--dem", required=True, type=Path,
                   help="DEM tiff path (container-side path)")
    p.add_argument("--safe-dir", default=Path("/data/S1-data"), type=Path,
                   help="directory containing the SAFE folders (container-side path)")
    p.add_argument("--burst-id", default=None,
                   help="burst id to use (default: first overlapping in both SAFEs)")
    p.add_argument("--pol", default="VV",
                   help="polarization (default: VV)")
    return p.parse_args()


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

    safe_ref_path = args.safe_dir / safe_ref
    safe_sec_path = args.safe_dir / safe_sec
    for s in (safe_ref_path, safe_sec_path):
        if not s.is_dir():
            raise SystemExit(f"SAFE dir not found: {s}")

    orbit_ref_path = _match_orbit(safe_ref, args.orbits_dir)
    orbit_sec_path = _match_orbit(safe_sec, args.orbits_dir)

    # Paths are container-side absolute already.
    safe_ref_c = str(safe_ref_path)
    safe_sec_c = str(safe_sec_path)
    orbit_ref_c = str(orbit_ref_path)
    orbit_sec_c = str(orbit_sec_path)
    dem_c = str(args.dem)

    # Reference runconfig (CPU + GPU).
    #
    # NOTE: product_path == scratch_path is a deliberate workaround for a
    # COMPASS radar-mode path bug. s1_geo2rdr writes range.off / azimuth.off
    # to <product>/<burst>/<date>/, but s1_resample reads them from
    # <scratch>/<burst>/<date>/. With separate dirs the secondary run fails
    # with "failed to create GDAL dataset from file .../range.off". Setting
    # both to the same root avoids the divergence. This finding is logged
    # for a possible upstream COMPASS report.
    for path in ("cpu", "gpu"):
        tpl = (CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml.template").read_text()
        unified = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/work"
        rendered = _substitute(tpl, {
            "SAFE_REF": safe_ref_c,
            "ORBIT_REF": orbit_ref_c,
            "DEM": dem_c,
            "BURST_ID": burst_id,
            "PRODUCT_PATH": unified,
            "SCRATCH_PATH": unified,
        })
        out = CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml"
        out.write_text(rendered)
        print(f"[render] wrote {out} (reference burst, {path})")

    # Secondary runconfig — coregistered to reference.
    # The COMPASS sec config's reference_burst.file_path must point at the
    # ref burst-date dir (containing radar_grid.txt), not the parent product/.
    # Layout produced by COMPASS ref run:
    #   <product>/<burst_id>/<YYYYMMDD>/{radar_grid.txt,*.slc.tif,x.tif,y.tif,z.tif,...}
    ref_records = [r for r in records
                   if r["safe"] == safe_ref and r["burst_id"] == burst_id and r["polarization"] == args.pol]
    if not ref_records:
        raise SystemExit(f"no record for ref burst {burst_id} pol={args.pol} in {safe_ref}")
    ref_date = ref_records[0]["sensing_start"][:10].replace("-", "")  # YYYYMMDD

    for path in ("cpu", "gpu"):
        tpl = (CFG_DIR / f"insar_s1_boso_cslc_{path}.yaml.template").read_text()
        unified_sec = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/work_sec"
        # Match the unified naming for the ref dir used above.
        ref_burst_date_dir = f"{LOGS_MOUNT}/scratch_s1_boso_cslc_{path}/work/{burst_id}/{ref_date}"
        rendered = _substitute(tpl, {
            "SAFE_REF": safe_sec_c,
            "ORBIT_REF": orbit_sec_c,
            "DEM": dem_c,
            "BURST_ID": burst_id,
            "PRODUCT_PATH": unified_sec,
            "SCRATCH_PATH": unified_sec,
        })
        rendered = rendered.replace(
            "is_reference: True\n                file_path:",
            f"is_reference: False\n                file_path: {ref_burst_date_dir}",
        )
        out = CFG_DIR / f"insar_s1_boso_cslc_sec_{path}.yaml"
        out.write_text(rendered)
        print(f"[render] wrote {out} (secondary burst, {path}, ref_date={ref_date})")

    print(f"[render] burst_id={burst_id} pol={args.pol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
