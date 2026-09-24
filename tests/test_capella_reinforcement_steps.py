"""Tests for scripts/capella_reinforcement_steps.sh.

Same contract as scripts/capella_rtc_steps.sh (tests/test_capella_rtc_steps.py):
a failing scene must fail the step and stop the loop. A stub ``python`` that
fails on the Nth call stands in for the real tools; no container, data or
isce3 needed. The GCOV step is also checked for fully rendered configs with
the right EPSG per site.
"""

import os
import pathlib
import re
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "capella_reinforcement_steps.sh"
N_CALLS = {"dem": 2, "convert": 5, "convert-beta0": 5, "geometry": 5, "gcov": 5,
           "multirtc": 3, "rtc-compare": 3}


def _stub(tmp_path, fail_on):
    counter = tmp_path / "count"
    counter.write_text("0")
    stub = tmp_path / "python"
    stub.write_text(f"""#!/usr/bin/env bash
n=$(( $(cat {counter}) + 1 )); echo $n > {counter}
[ "$n" -eq {fail_on} ] && exit 37
exit 0
""")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub, counter


def _run(tmp_path, step, fail_on):
    stub, counter = _stub(tmp_path, fail_on)
    env = dict(os.environ, REIN=str(tmp_path / "rein"), SCRATCH=str(tmp_path / "scratch"),
               PYTHON=str(stub), TIME="", MULTIRTC_SITE=str(tmp_path / "site"))
    p = subprocess.run(["bash", str(SCRIPT), step], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    return p, int(counter.read_text())


@pytest.mark.parametrize("step", sorted(N_CALLS))
def test_all_iterations_succeed(tmp_path, step):
    p, calls = _run(tmp_path, step, fail_on=0)
    assert p.returncode == 0, p.stderr
    assert calls == N_CALLS[step]


@pytest.mark.parametrize("step", sorted(N_CALLS))
@pytest.mark.parametrize("which", ["first", "middle"])
def test_a_failing_iteration_fails_the_step_and_stops(tmp_path, step, which):
    fail_on = 1 if which == "first" else 2
    p, calls = _run(tmp_path, step, fail_on=fail_on)
    assert p.returncode != 0
    assert "FAIL" in p.stderr
    assert calls == fail_on, "the loop must stop at the failing iteration"


def test_gcov_configs_are_fully_rendered(tmp_path):
    p, _ = _run(tmp_path, "gcov", fail_on=0)
    assert p.returncode == 0, p.stderr
    cfgs = sorted((tmp_path / "rein" / "configs").glob("gcov_*.yaml"))
    assert len(cfgs) == 5
    for cfg in cfgs:
        text = cfg.read_text()
        body = text[text.index("runconfig:"):]
        assert not re.search(r"\{[a-z_]+\}", body), f"placeholder left in {cfg.name}"
        epsg = re.search(r"output_epsg: (\d+)", body).group(1)
        site = "yumare" if cfg.stem == "gcov_20260627144157" else "niscemi"
        assert epsg == {"yumare": "32619", "niscemi": "32633"}[site]
        assert f"dem_{site}.tif" in body
        assert f"sas_output_file: {tmp_path}/rein/gcov/{cfg.stem[5:]}/{cfg.stem}.h5" in body


def test_unknown_step_is_a_usage_error(tmp_path):
    p, _ = _run(tmp_path, "nonsense", fail_on=0)
    assert p.returncode == 2
