"""Tests for tools/parse_insar_timing.py."""

import gzip
import textwrap

import pytest

from parse_insar_timing import (align_runs, closure, format_markdown,
                                parse_log)

# Bare completion lines (no journal channel headers): the parser must
# still produce the flat table this format allows.
CPU_LOG = textwrap.dedent("""\
     -- successfully ran bandpass_insar in 0.187 seconds
     -- successfully ran rdr2geo in 1053.790 seconds
     -- Successfully ran geo2rdr in 170.486 seconds
     -- successfully ran resample for frequency A in 336.811 seconds
     -- successfully ran resample in 336.815 seconds
     -- Successfully ran dense_offsets in 494.502 seconds
     -- successfully ran resample for frequency A in 497.299 seconds
     -- successfully ran resample in 497.371 seconds
     -- successfully ran crossmul in 500.078 seconds
     -- Successfully ran phase unwrapping in 1340.754 seconds
     -- successfully ran rdr2geo in 125.416 seconds
     -- successfully ran crossmul in 46.305 seconds
     -- successfully ran INSAR in 6101.784 seconds
""")

GPU_LOG = textwrap.dedent("""\
     -- successfully ran bandpass_insar in 0.150 seconds
     -- successfully ran rdr2geo in 100.000 seconds
     -- Successfully ran geo2rdr in 20.000 seconds
     -- successfully ran resample in 50.000 seconds
     -- Successfully ran dense_offsets in 60.000 seconds
     -- successfully ran resample in 70.000 seconds
     -- successfully ran crossmul in 80.000 seconds
     -- Successfully ran phase unwrapping in 1300.000 seconds
     -- successfully ran rdr2geo in 30.000 seconds
     -- successfully ran crossmul in 10.000 seconds
     -- successfully ran INSAR in 2000.000 seconds
""")

# Journal-formatted log exercising every bracket case observed in the
# real GUNW runs: a stage nested in another stage's timer (crossmul in
# unwrap), a two-level nest (rdr2geo/geo2rdr in prepare, itself inside
# the Ionosphere bracket), a non-inclusive timer (Ionosphere reports
# less than its bracket-children), a point event with no start marker
# (dense_offsets), and a non-.run channel whose "Starting ..." line
# must not open a bracket.
NESTED_LOG = textwrap.dedent("""\
    journal (insar.run):
     -- starting INSAR
    journal (rdr2geo.run):
     -- starting rdr2geo
    journal (rdr2geo.run):
     -- successfully ran rdr2geo in 100.000 seconds
    journal (isce.cuda.geometry.Geo2rdr):
     -- Starting acquisition time: 71785
    journal (dense_offsets.run):
     -- Successfully ran dense_offsets in 40.000 seconds
    journal (unwrap.run):
     -- Starting phase unwrapping
    journal (crossmul.run):
     -- starting crossmultipy
    journal (crossmul.run):
     -- successfully ran crossmul in 30.000 seconds
    journal (unwrap.run):
     -- Successfully ran phase unwrapping in 90.000 seconds
    journal (ionosphere_phase_correction.run):
     -- starting insar_ionosphere_correction
    journal (prepare_insar_hdf5.run):
     -- preparing InSAR HDF5 products
    journal (rdr2geo.run):
     -- starting rdr2geo
    journal (rdr2geo.run):
     -- successfully ran rdr2geo in 12.000 seconds
    journal (geo2rdr.run):
     -- starting geo2rdr
    journal (geo2rdr.run):
     -- Successfully ran geo2rdr in 3.000 seconds
    journal (prepare_insar_hdf5.run):
     -- successfully ran prepare_insar_hdf5 in 20.000 seconds
    journal (resample_slc_v2.run):
     -- starting resampling SLC
    journal (resample_slc_v2.run):
     -- successfully ran resample for frequency B in 4.900 seconds
    journal (resample_slc_v2.run):
     -- successfully ran resample in 5.000 seconds
    journal (ionosphere_phase_correction.run):
     -- successfully ran Ionosphere in 8.000 seconds
    journal (insar.run):
     -- successfully ran INSAR in 300.000 seconds
""")


@pytest.fixture
def cpu_entries(tmp_path):
    log = tmp_path / "cpu.log"
    log.write_text(CPU_LOG)
    entries, _ = parse_log(log)
    return entries


@pytest.fixture
def nested(tmp_path):
    log = tmp_path / "nested.log"
    log.write_text(NESTED_LOG)
    return parse_log(log)


def test_parse_log_matches_both_capitalisations(cpu_entries):
    stages = {entry["stage"] for entry in cpu_entries}
    assert "geo2rdr" in stages          # "Successfully"
    assert "rdr2geo" in stages          # "successfully"
    assert "phase unwrapping" in stages


def test_parse_log_drops_per_frequency_resample(cpu_entries):
    stages = [entry["stage"] for entry in cpu_entries]
    assert "resample for frequency A" not in stages
    assert stages.count("resample") == 2


def test_parse_log_indexes_repeated_stages(cpu_entries):
    rdr2geo = [entry for entry in cpu_entries if entry["stage"] == "rdr2geo"]
    assert [entry["occurrence"] for entry in rdr2geo] == [1, 2]
    assert rdr2geo[1]["seconds"] == pytest.approx(125.416)


def test_parse_log_without_channels_yields_flat_tree(cpu_entries):
    assert all(entry["parent"] is None for entry in cpu_entries)


def test_brackets_nest_crossmul_inside_unwrap(nested):
    entries, _ = nested
    by_key = {(e["stage"], e["occurrence"]): e for e in entries}
    assert by_key[("crossmul", 1)]["parent"] == ("phase unwrapping", 1)
    unwrap = by_key[("phase unwrapping", 1)]
    assert unwrap["timer_inclusive"] is True
    assert unwrap["self_seconds"] == pytest.approx(60.0)   # 90 - 30


def test_brackets_nest_two_levels_under_prepare(nested):
    entries, _ = nested
    by_key = {(e["stage"], e["occurrence"]): e for e in entries}
    assert by_key[("rdr2geo", 2)]["parent"] == ("prepare_insar_hdf5", 1)
    assert by_key[("geo2rdr", 1)]["parent"] == ("prepare_insar_hdf5", 1)
    prepare = by_key[("prepare_insar_hdf5", 1)]
    assert prepare["parent"] == ("Ionosphere", 1)
    assert prepare["self_seconds"] == pytest.approx(5.0)   # 20 - 12 - 3


def test_non_inclusive_timer_is_flagged(nested):
    entries, warnings = nested
    by_key = {(e["stage"], e["occurrence"]): e for e in entries}
    iono = by_key[("Ionosphere", 1)]
    # Bracket children (prepare 20 + resample 5) exceed the reported 8 s:
    # the Ionosphere timer starts only after its nested sub-chain.
    assert iono["timer_inclusive"] is False
    assert iono["self_seconds"] == pytest.approx(8.0)
    assert any("Ionosphere" in warning for warning in warnings)


def test_point_event_and_root_are_top_level(nested):
    entries, _ = nested
    by_key = {(e["stage"], e["occurrence"]): e for e in entries}
    # dense_offsets has no start marker: point event at top level.
    assert by_key[("dense_offsets", 1)]["parent"] is None
    # The INSAR root bracket is normalized away, not reported as parent.
    assert by_key[("rdr2geo", 1)]["parent"] is None
    assert by_key[("INSAR", 1)]["parent"] is None


def test_non_run_channel_never_opens_a_bracket(nested):
    entries, warnings = nested
    # "Starting acquisition time:" on isce.cuda.geometry.Geo2rdr must
    # not push a bracket (it would swallow dense_offsets and misparent
    # everything after it) nor leave an unclosed-start warning.
    assert not any("Geo2rdr" in warning for warning in warnings)
    by_key = {(e["stage"], e["occurrence"]): e for e in entries}
    assert by_key[("dense_offsets", 1)]["parent"] is None


def test_closure_sums_self_times_once(nested):
    entries, _ = nested
    result = closure(entries)
    # 100 + 40 + 60 + 30 + 5 + 12 + 3 + 5 + 8 = 263 (each stage once).
    assert result["total"] == pytest.approx(300.0)
    assert result["attributed"] == pytest.approx(263.0)
    assert result["unattributed"] == pytest.approx(37.0)


def test_parse_log_reads_gzip(tmp_path):
    log = tmp_path / "nested.log.gz"
    with gzip.open(log, "wt") as handle:
        handle.write(NESTED_LOG)
    entries, _ = parse_log(log)
    assert {(e["stage"], e["occurrence"]) for e in entries} >= {
        ("INSAR", 1), ("crossmul", 1), ("prepare_insar_hdf5", 1)}


def test_align_runs_pairs_by_stage_and_occurrence(tmp_path):
    cpu = tmp_path / "cpu.log"
    gpu = tmp_path / "gpu.log"
    cpu.write_text(CPU_LOG)
    gpu.write_text(GPU_LOG)
    rows = align_runs({"CPU": parse_log(cpu)[0], "GPU": parse_log(gpu)[0]})

    by_key = {(stage, occ): seconds for stage, occ, _, seconds in rows}
    assert by_key[("rdr2geo", 1)] == {"CPU": pytest.approx(1053.790),
                                      "GPU": pytest.approx(100.0)}
    assert by_key[("crossmul", 2)] == {"CPU": pytest.approx(46.305),
                                       "GPU": pytest.approx(10.0)}
    # The workflow total sorts last.
    assert rows[-1][0] == "INSAR"


def test_align_runs_emits_children_under_parent(tmp_path):
    log = tmp_path / "nested.log"
    log.write_text(NESTED_LOG)
    rows = align_runs({"RUN": parse_log(log)[0]})
    names = [(stage, occ, depth) for stage, occ, depth, _ in rows]
    # crossmul directly follows its parent unwrap, one level down.
    unwrap_at = names.index(("phase unwrapping", 1, 0))
    assert names[unwrap_at + 1] == ("crossmul", 1, 1)
    # prepare sits under Ionosphere, its own children one level deeper.
    iono_at = names.index(("Ionosphere", 1, 0))
    assert names[iono_at + 1] == ("prepare_insar_hdf5", 1, 1)
    assert names[iono_at + 2] == ("rdr2geo", 2, 2)


def test_format_markdown_adds_speedup_for_two_runs(tmp_path):
    cpu = tmp_path / "cpu.log"
    gpu = tmp_path / "gpu.log"
    cpu.write_text(CPU_LOG)
    gpu.write_text(GPU_LOG)
    runs = {"CPU": parse_log(cpu)[0], "GPU": parse_log(gpu)[0]}
    rows = align_runs(runs)
    table = format_markdown(rows, ["CPU", "GPU"])

    assert "| CPU/GPU |" in table.splitlines()[0]
    rdr2geo_row = next(line for line in table.splitlines()
                       if line.startswith("| rdr2geo |"))
    assert "10.54x" in rdr2geo_row
    assert "| rdr2geo #2 |" in table


def test_format_markdown_appends_closure_footer(tmp_path):
    log = tmp_path / "nested.log"
    log.write_text(NESTED_LOG)
    entries, _ = parse_log(log)
    table = format_markdown(align_runs({"RUN": entries}), ["RUN"],
                            {"RUN": closure(entries)})
    assert "37.0 s unattributed" in table
