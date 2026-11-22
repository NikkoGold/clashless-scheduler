"""`python -m scheduler` as a real program: the wiring the in-process tests skip (the entry point and exit codes).

Each run gets its own empty settings folder and working folder, and no settings variables.
"""
import os
import subprocess
import sys

import pytest

from scheduler.config import HOME_VAR, SETTING_VARS
from tests.helpers.cli_runner import ROOT, SAMPLE


def program(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k not in SETTING_VARS}
    env[HOME_VAR] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "scheduler", *map(str, args)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


@pytest.mark.slow
def test_the_documented_phrase_runs_and_exits_zero(tmp_path):
    done = program(tmp_path, SAMPLE, "no classes before 10, keep 12-13 free", "--offline")
    assert done.returncode == 0, done.stderr
    assert "Only 3 possible schedules match your constraints." in done.stdout


@pytest.mark.slow
def test_a_conflict_exits_one(tmp_path):
    done = program(tmp_path, SAMPLE, "fridays off", "--offline")
    assert done.returncode == 1, done.stderr
    assert "ZGC" in done.stdout


@pytest.mark.slow
def test_no_arguments_prints_usage_and_exits_two(tmp_path):
    done = program(tmp_path)
    assert done.returncode == 2 and "usage" in done.stderr.lower()


@pytest.mark.slow
def test_export_writes_the_chosen_schedule_to_a_workbook(tmp_path):
    from openpyxl import load_workbook

    done = program(tmp_path, SAMPLE, "no classes before 10, keep 12-13 free", "--offline", "--export", "out.xlsx",
                   "--export-rank", "2")
    assert done.returncode == 0, done.stderr
    assert "Exported schedule 2 to out.xlsx" in done.stdout
    assert load_workbook(tmp_path / "out.xlsx").sheetnames == ["Schedule", "Sessions", "Rules"]
