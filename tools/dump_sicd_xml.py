#!/usr/bin/env python3
"""Dump the SICD XML metadata out of a NITF 2.1 file, locally or over HTTP.

SICD stores its metadata as an XML Data Extension Segment (DES) at the tail of
the NITF container, after the image data. The NITF file header carries every
segment length, so the DES byte range can be computed from the first ~1 KB of
the file and fetched with a single HTTP range request -- no need to download
the 800 MB of pixels first.

This deliberately avoids sarpy/sarkit: it must work before either is installed
in the dev image, and it is also the escape hatch if a producer writes a SICD
version the reader of the day rejects.

Usage:
    tools/dump_sicd_xml.py <path-or-url> [-o OUT.xml]
    tools/dump_sicd_xml.py <path-or-url> --header-only
"""

from __future__ import annotations

import argparse
import sys
from urllib.request import Request, urlopen

# NITF 2.1 file header, up to the field that tells us how long it is. Every
# field is fixed width ASCII; see MIL-STD-2500C table A-1.
_FIXED_HEAD = [
    ("FHDR", 4), ("FVER", 5), ("CLEVEL", 2), ("STYPE", 4), ("OSTAID", 10),
    ("FDT", 14), ("FTITLE", 80),
    ("FSCLAS", 1), ("FSCLSY", 2), ("FSCODE", 11), ("FSCTLH", 2), ("FSREL", 20),
    ("FSDCTP", 2), ("FSDCDT", 8), ("FSDCXM", 4), ("FSDG", 1), ("FSDGDT", 8),
    ("FSCLTX", 43), ("FSCATP", 1), ("FSCAUT", 40), ("FSCRSN", 1),
    ("FSSRDT", 8), ("FSCTLN", 15),
    ("FSCOP", 5), ("FSCPYS", 5), ("ENCRYP", 1), ("FBKGC", 3),
    ("ONAME", 24), ("OPHONE", 18), ("FL", 12), ("HL", 6),
]

# Segment count fields and the (subheader, data) length widths that follow each.
_SEGMENTS = [
    ("NUMI", 6, 10),   # image segments
    ("NUMS", 4, 6),    # graphic segments
    ("NUMX", 0, 0),    # reserved, always 000 with no entries
    ("NUMT", 4, 5),    # text segments
    ("NUMDES", 4, 9),  # data extension segments
]


def _read_range(source: str, start: int, length: int) -> bytes:
    """Read `length` bytes at `start`, from a local path or an http(s) URL."""
    if source.startswith(("http://", "https://")):
        end = start + length - 1
        req = Request(source, headers={"Range": f"bytes={start}-{end}"})
        with urlopen(req) as resp:
            if resp.status not in (200, 206):
                raise RuntimeError(f"unexpected HTTP status {resp.status}")
            if resp.status == 200:
                # Server ignored the Range header; do not pull the whole file.
                raise RuntimeError("server ignored Range request")
            return resp.read()
    with open(source, "rb") as fh:
        fh.seek(start)
        return fh.read(length)


def parse_nitf_header(head: bytes) -> dict:
    """Parse the NITF file header far enough to locate the DES segments."""
    if head[:4] != b"NITF":
        raise ValueError(f"not a NITF file (magic {head[:4]!r})")

    pos = 0
    fields: dict[str, str] = {}
    for name, width in _FIXED_HEAD:
        fields[name] = head[pos:pos + width].decode("ascii", "replace")
        pos += width

    offsets = {}
    # Running offset of the next segment's subheader within the file. Segments
    # are laid out in header order: image, graphic, text, DES.
    cursor = int(fields["HL"])
    for count_name, sub_w, data_w in _SEGMENTS:
        count = int(head[pos:pos + 3])
        pos += 3
        entries = []
        for _ in range(count):
            sub_len = int(head[pos:pos + sub_w]) if sub_w else 0
            pos += sub_w
            data_len = int(head[pos:pos + data_w]) if data_w else 0
            pos += data_w
            entries.append({
                "subheader_offset": cursor,
                "subheader_length": sub_len,
                "data_offset": cursor + sub_len,
                "data_length": data_len,
            })
            cursor += sub_len + data_len
        offsets[count_name] = entries

    fields["_segments"] = offsets
    fields["_header_parsed_bytes"] = pos
    return fields


def extract_sicd_xml(source: str) -> tuple[str, dict]:
    """Return (xml_text, header_fields) for the first XML DES in the file."""
    # 2 KB covers the fixed header plus the segment tables of any SICD we have
    # seen (a handful of image segments at most).
    head = _read_range(source, 0, 2048)
    fields = parse_nitf_header(head)

    des_entries = fields["_segments"]["NUMDES"]
    if not des_entries:
        raise ValueError("file has no data extension segments")

    for des in des_entries:
        # The DES subheader begins with DE (2) + DESID (25) + DESVER (2).
        sub = _read_range(source, des["subheader_offset"],
                          min(des["subheader_length"], 200))
        desid = sub[2:27].decode("ascii", "replace").strip()
        if "XML" not in desid.upper():
            continue
        raw = _read_range(source, des["data_offset"], des["data_length"])
        text = raw.decode("utf-8", "replace")
        start = text.find("<")
        if start < 0:
            continue
        return text[start:], fields

    raise ValueError("no XML data extension segment found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="local NITF path or http(s) URL")
    parser.add_argument("-o", "--output", help="write the XML here (default: stdout)")
    parser.add_argument("--header-only", action="store_true",
                        help="print the parsed NITF segment layout and stop")
    args = parser.parse_args(argv)

    if args.header_only:
        head = _read_range(args.source, 0, 2048)
        fields = parse_nitf_header(head)
        print(f"FHDR/FVER   {fields['FHDR']} {fields['FVER']}")
        print(f"FTITLE      {fields['FTITLE'].strip()}")
        print(f"FL / HL     {int(fields['FL'])} / {int(fields['HL'])}")
        for name, entries in fields["_segments"].items():
            for i, e in enumerate(entries):
                print(f"{name}[{i}]   subheader @{e['subheader_offset']} "
                      f"+{e['subheader_length']}  data @{e['data_offset']} "
                      f"+{e['data_length']}")
        return 0

    xml, _ = extract_sicd_xml(args.source)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(xml)
        print(f"wrote {len(xml)} bytes to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
