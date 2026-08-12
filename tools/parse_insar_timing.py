#!/usr/bin/env python3
"""Extract per-stage wall times from ``nisar.workflows.insar`` logs.

The insar workflow reports each finished stage through journal lines
of the form ``-- [Ss]uccessfully ran <stage> in <sec> seconds`` (the
capitalisation varies by stage).  Stage names repeat when a stage runs
more than once — the ionosphere chain re-runs rdr2geo, resample and
crossmul for frequency B — so occurrences are indexed in log order and
runs are aligned by ``(stage, occurrence)``.

Stage timers are NOT all at the same level.  Some stages invoke other
stages while their own timer is running:

* ``phase unwrapping`` re-runs ``crossmul`` at the unwrap look factor;
* ``prepare_insar_hdf5`` runs ``rdr2geo`` + ``geo2rdr`` when the
  geometric offsets are missing from its scratch dir (the ionosphere
  chain hits this: its rdr2geo symlink is created only *after*
  ``prepare_insar_hdf5.run``);
* the whole ionosphere sub-chain runs between the ``Ionosphere``
  start marker and its completion line.

Summing such a parent together with its children double-counts the
children.  This parser therefore tracks the ``starting <stage>`` /
``successfully ran <stage>`` bracket pairs (matched per journal
channel with a stack) and reports each occurrence's parent, so tables
can be summed correctly: every row contributes its *self* time,

    self = reported - sum(children reported)     (inclusive timer)
    self = reported                              (non-inclusive timer)

where a timer is *non-inclusive* when its reported seconds are smaller
than the sum of its bracket-children — ``Ionosphere`` is the known
case: its ``t_all`` starts only after the nested sub-chain has run, so
its reported time excludes the children that its log bracket contains.

Stages that emit no start marker (``dense_offsets``, ``polyfit
rubbersheet``) cannot be bracketed and are attached as point events to
whatever bracket is open around their completion line.

Per-frequency ``resample for frequency X`` lines duplicate the
``resample`` stage total that immediately follows them and are
dropped.  Logs may be plain text or gzip-compressed (``.gz``).

CLI usage::

    python tools/parse_insar_timing.py LOG [LOG ...] \
        [--labels NAME [NAME ...]] [--json out.json]

With two logs the table gains a speedup column (first log / second
log), so pass the baseline (CPU) log first.
"""

import argparse
import gzip
import json
import pathlib
import re
import sys

TIMING_RE = re.compile(
    r"--\s+[Ss]uccessfully ran (?P<stage>.+?) in (?P<sec>[0-9.]+) seconds")

# ``journal (channel):`` header line; the message follows on the next line(s).
CHANNEL_RE = re.compile(r"^journal \((?P<chan>[^)]+)\):\s*$")

# Start markers appear only on ``*.run`` channels; ``preparing`` covers
# prepare_insar_hdf5, which does not use the word "starting".
START_RE = re.compile(r"^\s*--\s+(?:[Ss]tarting|preparing)\s+\S")

# Redundant per-frequency detail lines (their stage total follows).
EXCLUDE_RE = re.compile(r"^resample for frequency")

# The workflow-wide total reported by nisar.workflows.insar at exit.
TOTAL_STAGE = "INSAR"

# Reported/child mismatches below this are float noise, not structure.
EPSILON = 1e-6


def _read_lines(path):
    """Yield the lines of a plain or gzip-compressed log file."""
    path = pathlib.Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as handle:
        yield from handle


def parse_log(path):
    """Parse one insar.log into an ordered list of stage timings.

    Args:
        path: Path to an ``insar.log`` written by the insar workflow
            (optionally gzip-compressed).

    Returns:
        Tuple of (entries, warnings).  ``entries`` is a list of::

            {"stage": str, "occurrence": int, "seconds": float,
             "parent": (stage, occurrence) or None,
             "self_seconds": float,
             "timer_inclusive": bool or None,   # None for leaves
             "start_line": int or None, "end_line": int}

        in log order, occurrence counting from 1 per stage name.
        ``warnings`` lists structural anomalies found while matching
        brackets (unclosed starts, interleaved completions).
    """
    counts = {}
    entries = []
    stack = []          # open brackets: {"chan", "line", "entry": None}
    warnings = []
    channel = None
    for lineno, line in enumerate(_read_lines(path), start=1):
        header = CHANNEL_RE.match(line)
        if header:
            channel = header.group("chan")
            continue
        timing = TIMING_RE.search(line)
        if timing:
            stage = timing.group("stage").strip()
            if EXCLUDE_RE.match(stage):
                continue
            counts[stage] = counts.get(stage, 0) + 1
            entry = {"stage": stage,
                     "occurrence": counts[stage],
                     "seconds": float(timing.group("sec")),
                     "parent": None,
                     "start_line": None,
                     "end_line": lineno}
            if stack and stack[-1]["chan"] == channel:
                frame = stack.pop()
                frame["entry"] = entry
                entry["start_line"] = frame["line"]
            elif any(frame["chan"] == channel for frame in stack):
                warnings.append(
                    f"line {lineno}: completion on channel {channel!r} "
                    "interleaves an outer open bracket; treated as a "
                    "point event")
            if stack:
                entry["parent"] = stack[-1]
            entries.append(entry)
            continue
        # Start markers only ever come from workflow channels (*.run);
        # this also rejects e.g. "Starting acquisition time:" emitted
        # by isce.cuda.geometry.Geo2rdr.
        if channel and channel.endswith(".run") and START_RE.match(line):
            stack.append({"chan": channel, "line": lineno, "entry": None})

    for frame in stack:
        warnings.append(
            f"line {frame['line']}: start on channel {frame['chan']!r} "
            "was never completed")

    # Resolve parent frames to (stage, occurrence) keys; a parent frame
    # without an entry means its bracket never closed.  The workflow
    # root (INSAR) brackets every stage; normalize it away so regular
    # stages read as top level and the total stays a separate row.
    for entry in entries:
        frame = entry["parent"]
        if frame is not None:
            parent_entry = frame["entry"]
            entry["parent"] = (None if parent_entry is None else
                               (parent_entry["stage"],
                                parent_entry["occurrence"]))
        if entry["parent"] is not None and entry["parent"][0] == TOTAL_STAGE:
            entry["parent"] = None

    _attribute_self_times(entries, warnings)
    return entries, warnings


def _attribute_self_times(entries, warnings):
    """Annotate entries with self_seconds and timer inclusiveness.

    A parent whose reported seconds cover its children (the normal
    case: the timer wraps the whole stage body) gets ``self = reported
    - children``.  A parent reporting *less* than its children has a
    timer that starts after the nested calls (``Ionosphere``); its
    reported time is already exclusive, so ``self = reported``.
    """
    children = {}
    for entry in entries:
        if entry["parent"] is not None:
            children[entry["parent"]] = (
                children.get(entry["parent"], 0.0) + entry["seconds"])
    for entry in entries:
        child_sum = children.get((entry["stage"], entry["occurrence"]), 0.0)
        if child_sum == 0.0:
            entry["timer_inclusive"] = None
            entry["self_seconds"] = entry["seconds"]
        elif entry["seconds"] + EPSILON >= child_sum:
            entry["timer_inclusive"] = True
            entry["self_seconds"] = entry["seconds"] - child_sum
        else:
            entry["timer_inclusive"] = False
            entry["self_seconds"] = entry["seconds"]
            warnings.append(
                f"{entry['stage']} #{entry['occurrence']}: reported "
                f"{entry['seconds']:.3f} s < nested children "
                f"{child_sum:.3f} s — timer excludes its children; "
                "row kept as self time")


def closure(entries):
    """Compute the accounting closure of one parsed run.

    Args:
        entries: Entry list from :func:`parse_log`.

    Returns:
        Dict with ``total`` (the INSAR row, None if absent),
        ``attributed`` (sum of every non-total row's self time) and
        ``unattributed`` (total - attributed, None without a total).
    """
    total = next((entry["seconds"] for entry in entries
                  if entry["stage"] == TOTAL_STAGE), None)
    attributed = sum(entry["self_seconds"] for entry in entries
                     if entry["stage"] != TOTAL_STAGE)
    return {"total": total,
            "attributed": attributed,
            "unattributed": None if total is None else total - attributed}


def align_runs(runs):
    """Align several parsed runs on their (stage, occurrence) keys.

    The tree structure (parents, sibling order) is taken from the
    first run that contains each node; a warning is appropriate when
    runs disagree, but runs of the same workflow produce identical
    structures in practice.

    Args:
        runs: Dict of label -> entry list from :func:`parse_log`.

    Returns:
        List of ``(stage, occurrence, depth, {label: seconds_or_None})``
        in depth-first tree order (children under their parent), the
        workflow total last.
    """
    nodes = {}          # key -> {"parent", "order", "seconds": {label: s}}
    for label, entries in runs.items():
        for position, entry in enumerate(entries):
            key = (entry["stage"], entry["occurrence"])
            if key not in nodes:
                nodes[key] = {"parent": entry["parent"],
                              "order": (entry["start_line"] or
                                        entry["end_line"] or position),
                              "seconds": {}}
            nodes[key]["seconds"][label] = entry["seconds"]

    def emit(parent, depth, out):
        siblings = [(key, node) for key, node in nodes.items()
                    if node["parent"] == parent and key[0] != TOTAL_STAGE]
        for key, node in sorted(siblings, key=lambda item: item[1]["order"]):
            out.append((key[0], key[1], depth, node["seconds"]))
            emit(key, depth + 1, out)

    rows = []
    emit(None, 0, rows)
    # Point events under an unclosed bracket resolve their parent to
    # None as well, so the root pass above already covers them.
    for key, node in nodes.items():
        if key[0] == TOTAL_STAGE:
            rows.append((key[0], key[1], 0, node["seconds"]))
    return rows


def format_markdown(rows, labels, closures=None):
    """Render aligned rows as a GitHub-flavored markdown table.

    Children are indented under their parent with a ``└`` prefix (the
    indent uses no-break spaces, which GFM keeps).  Summing the table
    means summing top-level rows only — a child's time is already
    inside its parent (except where a non-inclusive timer was flagged
    by :func:`parse_log`).

    Args:
        rows: Aligned rows from :func:`align_runs`.
        labels: Column labels, one per run.
        closures: Optional dict of label -> :func:`closure` result,
            appended as an accounting footer.
    """
    with_speedup = len(labels) == 2
    header = ["stage"] + list(labels)
    if with_speedup:
        header.append(f"{labels[0]}/{labels[1]}")
    lines = ["| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
    for stage, occ, depth, seconds in rows:
        name = stage if occ == 1 else f"{stage} #{occ}"
        if depth:
            name = "  " * (depth - 1) + "└ " + name
        cells = [name]
        for label in labels:
            value = seconds.get(label)
            cells.append("-" if value is None else f"{value:.1f}")
        if with_speedup:
            first, second = (seconds.get(label) for label in labels)
            if first is not None and second:
                cells.append(f"{first / second:.2f}x")
            else:
                cells.append("-")
        lines.append("| " + " | ".join(cells) + " |")
    if closures:
        lines.append("")
        for label in labels:
            result = closures.get(label)
            if result is None or result["total"] is None:
                continue
            share = result["unattributed"] / result["total"] * 100.0
            lines.append(
                f"- {label}: self-time sum {result['attributed']:.1f} s "
                f"vs {TOTAL_STAGE} {result['total']:.1f} s -> "
                f"{result['unattributed']:.1f} s unattributed "
                f"({share:.2f} %)")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Per-stage wall-time table from insar.log files.")
    parser.add_argument("logs", nargs="+", type=pathlib.Path,
                        help="insar.log paths, .gz accepted (baseline first)")
    parser.add_argument("--labels", nargs="+",
                        help="column labels (default: log parent dir names)")
    parser.add_argument("--json", type=pathlib.Path,
                        help="also dump the parsed timings as JSON")
    args = parser.parse_args(argv)

    labels = args.labels or [path.resolve().parent.name for path in args.logs]
    if len(labels) != len(args.logs):
        parser.error("number of --labels must match number of logs")

    runs = {}
    for label, path in zip(labels, args.logs):
        entries, warnings = parse_log(path)
        runs[label] = entries
        for warning in warnings:
            print(f"warning [{label}]: {warning}", file=sys.stderr)

    closures = {label: closure(entries) for label, entries in runs.items()}
    rows = align_runs(runs)
    print(format_markdown(rows, labels, closures))

    if args.json:
        serializable = {
            label: [dict(entry, parent=(list(entry["parent"])
                                        if entry["parent"] else None))
                    for entry in entries]
            for label, entries in runs.items()}
        args.json.write_text(json.dumps(serializable, indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
