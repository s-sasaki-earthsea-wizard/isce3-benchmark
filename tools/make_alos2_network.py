#!/usr/bin/env python3
"""Generate the all-pairs InSAR network for the ALOS-2 Kujukuri stack.

Writes one isce3 insar runconfig per date-ordered pair (i < j) from the
template, plus a pairs manifest in the format nisar-displacement's
``closure_network.py`` reads (``dates``, ``rslc``, ``pairs[].gunw``), so the
GUNWs can be handed straight to that project's loop-closure evaluation.

Substitution is textual (``str.format_map``) for the same reason as
nisar-displacement's make_pair_runconfigs.py: a YAML round-trip would
rewrite the null keys and float literals the schema needs.

Usage::

    python tools/make_alos2_network.py \
        --rslc-dir data/ALOS2-kujukuri/rslc_crop \
        --template configs/insar_alos2_kujukuri_template.yaml \
        --out-dir configs/alos2_kujukuri \
        --gunw-root /abs/path/to/data/ALOS2-kujukuri/gunw \
        --manifest data/ALOS2-kujukuri/pairs_ALOS2_kujukuri.json \
        [--dates 20151020 20151103 20160531] [--max-days N]
"""

import argparse
import itertools
import json
import pathlib
import re
from datetime import date

PLACEHOLDERS = ("name", "reference_rslc", "secondary_rslc")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
CAMPAIGN = "alos2_kujukuri"


def iso(d8):
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"


def check_template(text):
    found = set(_PLACEHOLDER_RE.findall(text))
    unknown = sorted(found - set(PLACEHOLDERS))
    missing = sorted(set(PLACEHOLDERS) - found)
    if unknown or missing:
        raise ValueError(f"template placeholder mismatch: unknown={unknown} "
                         f"missing={missing}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rslc-dir", type=pathlib.Path, required=True,
                    help="directory of RSLCs named YYYYMMDD.h5")
    ap.add_argument("--template", type=pathlib.Path, required=True)
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    ap.add_argument("--gunw-root", required=True,
                    help="absolute host path under which the runner writes "
                         "<name>/product.h5 (recorded in the manifest)")
    ap.add_argument("--manifest", type=pathlib.Path, required=True)
    ap.add_argument("--dates", nargs="*", default=None,
                    help="subset of YYYYMMDD dates (default: every RSLC found)")
    ap.add_argument("--max-days", type=int, default=None,
                    help="drop pairs whose temporal baseline exceeds this")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    template = args.template.read_text(encoding="utf-8")
    check_template(template)

    found = sorted(p.stem for p in args.rslc_dir.glob("????????.h5"))
    dates = args.dates or found
    missing = [d for d in dates if d not in found]
    if missing:
        raise SystemExit(f"no RSLC in {args.rslc_dir} for {missing}")
    dates = sorted(dates)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pairs = []
    for ref, sec in itertools.combinations(dates, 2):
        days = (date.fromisoformat(iso(sec)) - date.fromisoformat(iso(ref))).days
        if args.max_days is not None and days > args.max_days:
            continue
        name = f"gunw_{CAMPAIGN}_{ref}_{sec}"
        cfg = args.out_dir / f"insar_{name}.yaml"
        if cfg.exists() and not args.force:
            raise SystemExit(f"{cfg} exists (use --force)")
        cfg.write_text(template.format_map({
            "name": name,
            "reference_rslc": f"{ref}.h5",
            "secondary_rslc": f"{sec}.h5",
        }), encoding="utf-8")
        pairs.append({
            "ref": iso(ref), "sec": iso(sec), "days": days,
            "gunw": f"{args.gunw_root.rstrip('/')}/{name}/product.h5",
            "asf_gunw": None,
        })

    manifest = {
        "campaign": f"closure-network-{CAMPAIGN}",
        "sensor": "ALOS-2 PALSAR-2",
        "mode": "Stripmap Ultra-fine (UBS), HH, 79.4 MHz",
        "track": 0, "frame": 700, "direction": "A",
        "note": (f"JAXA PALSAR-2 sample time series over Kujukuri (Chiba), "
                 f"{len(dates)} dates, all C(n,2)={len(pairs)} date-ordered "
                 f"pairs self-run with the isce3 v0.25.16 chain "
                 f"(configs/insar_alos2_kujukuri_template.yaml) on the full "
                 f"scenes converted by alos2_to_nisar_l1.py. No TEC / water mask."),
        "dates": [iso(d) for d in dates],
        "rslc": {iso(d): f"{d}.h5" for d in dates},
        "rslc_dir": str(args.rslc_dir.resolve()),
        "pairs": pairs,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    print(f"{len(dates)} dates -> {len(pairs)} pairs; runconfigs in "
          f"{args.out_dir}, manifest {args.manifest}")


if __name__ == "__main__":
    main()
