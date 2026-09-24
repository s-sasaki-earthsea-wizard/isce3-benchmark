#!/usr/bin/env python3
"""Intervention for the geometry cross-check: set a SICD's COA time to its CA time.

For RGZERO/INCA, ``tools/sicd_to_nisar_rslc.py`` maps image columns to
zero-Doppler time through ``RMA/INCA/TimeCAPoly``, and isce3 projects on that
zero-Doppler grid. sarkit's ``image_to_constant_hae_surface`` instead projects
each pixel at its centre-of-aperture time from ``Grid/TimeCOAPoly``, through the
INCA R/Rdot model (``DRateSFPoly``). Where the two times differ, the two
projections can differ by more than the converter itself would explain.

This tool reads the SICD XML stored in a converted RSLC (or a .xml file) and
writes a copy whose ``Grid/TimeCOAPoly`` equals ``TimeCAPoly`` as a function of
the column coordinate only, i.e. TimeCOAPoly(x, y) := TimeCAPoly(y). Running the
sarkit half of ``tools/sicd_rslc_geometry_check.py`` on the copy tests whether
the COA-time path accounts for the isce3-vs-sarkit difference.

Usage (host, venv-sarkit for lxml)::

    python tools/sicd_coa_to_ca.py rslc.h5 --out coa_eq_ca.sicd.xml
"""

from __future__ import annotations

import argparse
import io
import sys

import h5py
import lxml.etree as et

INPUTS = "science/LSAR/RSLC/metadata/processingInformation/inputs"


def xml_text(src: str) -> str:
    if src.endswith(".xml"):
        return open(src, encoding="utf-8").read()
    with h5py.File(src, "r") as fid:
        raw = fid[f"{INPUTS}/sicdXml"][()]
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="converted RSLC (.h5) or SICD XML (.xml)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    tree = et.parse(io.BytesIO(xml_text(args.src).encode("utf-8")))
    root = tree.getroot()
    ns = {"s": root.nsmap[None]} if None in root.nsmap else {}
    p = "s:" if ns else ""
    tca = root.find(f"{p}RMA/{p}INCA/{p}TimeCAPoly", ns)
    tcoa = root.find(f"{p}Grid/{p}TimeCOAPoly", ns)
    if tca is None or tcoa is None:
        print("TimeCAPoly or TimeCOAPoly not found", file=sys.stderr)
        return 1
    coefs = {int(c.get("exponent1")): c.text for c in tca}
    for c in list(tcoa):
        tcoa.remove(c)
    tcoa.set("order1", "0")
    tcoa.set("order2", str(max(coefs)))
    tag = f"{{{ns['s']}}}Coef" if ns else "Coef"
    for j in sorted(coefs):
        c = et.SubElement(tcoa, tag)
        c.set("exponent1", "0")
        c.set("exponent2", str(j))
        c.text = coefs[j]
    tree.write(args.out, xml_declaration=True, encoding="UTF-8")
    print(f"wrote {args.out}: TimeCOAPoly(x, y) := TimeCAPoly(y), {len(coefs)} coefficients")
    return 0


if __name__ == "__main__":
    sys.exit(main())
