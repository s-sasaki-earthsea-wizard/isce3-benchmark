#!/usr/bin/env python3
"""Fetch NISAR ancillary granules (TEC, MOE orbit, ...) from ASF DAAC.

Resolves each granule name through the CMR granule search API (no auth) and
downloads the primary data file via Earthdata Login. Credentials are read by
curl from ``~/.netrc`` (machine urs.earthdata.nasa.gov) — this script never
reads or prints them.

Usage:
    python fetch/fetch_nisar_ancillary.py --out data/NISAR/ancillary \
        NISAR_ANC_TEC_20260717T211944_20260715T230002_20260716T235952_s015 \
        NISAR_ANC_J_PR_MOE_20260717T132621_20260715T205942_20260717T025942

The granule names for a given official GCOV product are recorded in its
embedded runconfig (``.../parameters/runConfigurationContents`` →
``dynamic_ancillary_file_group``).
"""

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CMR = 'https://cmr.earthdata.nasa.gov/search/granules.json'


def resolve_url(granule: str) -> str:
    """Return the public HTTPS data URL for a granule name via CMR."""
    q = urllib.parse.urlencode({
        'readable_granule_name': granule,
        'provider': 'ASF',
        'page_size': 5,
    })
    with urllib.request.urlopen(f'{CMR}?{q}', timeout=30) as r:
        feed = json.load(r)
    for entry in feed['feed']['entry']:
        for link in entry.get('links', []):
            href = link.get('href', '')
            # primary data file: public HTTPS, not the *-PRIVATE-DATA mirrors
            if (link.get('rel', '').endswith('/data#')
                    and href.startswith('https://')
                    and 'PRIVATE' not in href
                    and href.rstrip('/').split('/')[-1].startswith(granule)):
                return href
    raise LookupError(f'no public data URL found in CMR for {granule}')


def fetch(url: str, out_dir: Path) -> Path:
    out = out_dir / url.rstrip('/').split('/')[-1]
    with tempfile.NamedTemporaryFile(suffix='.cookies') as jar:
        subprocess.run(
            ['curl', '-sS', '-n', '-L', '-c', jar.name, '-b', jar.name,
             '-o', str(out), url],
            check=True)
    # Earthdata failures tend to come back as small HTML/JSON error bodies
    head = out.read_bytes()[:200].lstrip().lower()
    if head.startswith(b'<!doctype') or head.startswith(b'<html'):
        raise RuntimeError(
            f'{out.name}: got an HTML page instead of data — check the '
            'urs.earthdata.nasa.gov entry in ~/.netrc')
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('granules', nargs='+', help='ancillary granule name(s)')
    ap.add_argument('--out', type=Path, required=True, help='output directory')
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for granule in args.granules:
        url = resolve_url(granule)
        print(f'[fetch] {granule}\n        <- {url}')
        out = fetch(url, args.out)
        print(f'        -> {out} ({out.stat().st_size/1e6:.1f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
