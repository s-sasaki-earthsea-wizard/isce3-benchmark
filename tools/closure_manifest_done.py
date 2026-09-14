#!/usr/bin/env python3
"""Subset a closure-network manifest to the pairs that have finished.

nisar-displacement's ``closure_network.py`` drops triangles whose legs are
missing from the manifest (``closable_triangles``), but it opens every
GUNW the manifest lists. While the 66-pair batch is still running, feed it
a manifest that only carries the pairs whose output directory has the
runner's ``.complete`` marker. Optionally restrict to the dates whose
pairs are ALL done (a complete sub-clique), which is the cleanest input
for the attribution step.

Usage::

    python tools/closure_manifest_done.py \
        configs/alos2_kujukuri/pairs_ALOS2_kujukuri.json \
        --out /path/to/pairs_done.json [--complete-clique]

Prints a one-line summary (dates, pairs, closable triangles).
"""

import argparse
import itertools
import json
import pathlib


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--complete-clique", action="store_true",
                    help="keep only the largest date set whose pairs are all done")
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text(encoding="utf-8"))
    done = [p for p in m["pairs"]
            if (pathlib.Path(p["gunw"]).parent / ".complete").is_file()]
    dates = list(m["dates"])
    idx = {d: i for i, d in enumerate(dates)}
    have = {(idx[p["ref"]], idx[p["sec"]]) for p in done}

    if args.complete_clique:
        best = ()
        for size in range(len(dates), 2, -1):
            for sub in itertools.combinations(range(len(dates)), size):
                if all(e in have for e in itertools.combinations(sub, 2)):
                    best = sub
                    break
            if best:
                break
        keep = set(best)
        done = [p for p in done if idx[p["ref"]] in keep and idx[p["sec"]] in keep]
        dates = [d for d in dates if idx[d] in keep]
        have = {(idx[p["ref"]], idx[p["sec"]]) for p in done}

    tri = sum(1 for a, b, c in itertools.combinations(sorted({i for e in have for i in e}), 3)
              if (a, b) in have and (b, c) in have and (a, c) in have)

    out = dict(m)
    out["dates"] = dates
    out["rslc"] = {d: m["rslc"][d] for d in dates}
    out["pairs"] = done
    out["note"] = (m.get("note", "") +
                   f" SUBSET written by closure_manifest_done.py: {len(done)} finished "
                   f"pairs over {len(dates)} dates, {tri} closable triangles"
                   + (" (complete clique only)." if args.complete_clique else "."))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{len(dates)} dates, {len(done)} finished pairs, {tri} closable triangles -> {args.out}")


if __name__ == "__main__":
    main()
