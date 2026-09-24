"""Tests for scripts/capella_rtc_steps.sh: a failing iteration must fail the step.

The loops used to be ``python ... && echo OK`` inside Makefile recipes, which
reported success whenever the *last* iteration succeeded (found in the PR #57
review of the RTC work). A stub ``python`` that fails on the Nth call stands in for the real
tools; no container, data or isce3 needed.
"""

import os
import pathlib
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "capella_rtc_steps.sh"
N_CALLS = {"convert-beta0": 2}


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
    env = dict(os.environ, CAPELLA_DATA=str(tmp_path / "data"), PYTHON=str(stub),
               MULTIRTC_SITE=str(tmp_path / "site"))
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


def test_unknown_step_is_a_usage_error(tmp_path):
    p, _ = _run(tmp_path, "nonsense", fail_on=0)
    assert p.returncode == 2
