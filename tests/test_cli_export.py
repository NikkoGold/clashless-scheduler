"""--export and --export-rank: write the schedule at any rank to .xlsx or .json, and refuse what can't work before
solving. The exact counts are the ones documented in data/README.md ("no classes before 10, keep 12-13 free" gives 3).
"""
import json

import pytest
from openpyxl import load_workbook

from scheduler import export
from scheduler.models import Schedule, parse_constraint
from tests.helpers.cli_runner import SAMPLE, cli
from tests.helpers.fake_console import FakeConsole
from tests.helpers.invariants import assert_valid_results

pytestmark = pytest.mark.usefixtures("fresh_settings")

THREE = "no classes before 10, keep 12-13 free"


def checked(run, k=5):
    o = run.outcome
    assert_valid_results(o.result, o.catalog, o.constraints, k)
    return run


def test_export_writes_schedule_one_by_default(tmp_path):
    path = tmp_path / "out.json"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path))
    assert run.code == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rank"] == 1 and Schedule.model_validate(data["schedule"]) == run.outcome.result.schedules[0]
    assert [parse_constraint(c) for c in data["constraints"]] == run.outcome.constraints
    assert f"Exported schedule 1 to {path}" in run.out


@pytest.mark.parametrize("rank", [2, 3])
def test_export_rank_writes_that_schedule(tmp_path, rank):
    path = tmp_path / "out.json"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path, "--export-rank", rank))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rank"] == rank
    assert Schedule.model_validate(data["schedule"]) == run.outcome.result.schedules[rank - 1]
    assert f"Exported schedule {rank} to {path}" in run.out


def test_export_to_xlsx_writes_a_workbook_titled_with_the_rank(tmp_path):
    path = tmp_path / "plan.xlsx"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path, "--export-rank", "2"))
    assert run.code == 0
    assert load_workbook(path)[export.GRID_SHEET]["A1"].value.startswith("Schedule 2 · ")


@pytest.mark.parametrize("name", ["out.pdf", "out.csv", "out"])
def test_an_unsupported_extension_stops_before_solving(tmp_path, name):
    run = cli(SAMPLE, THREE, "--offline", "--export", tmp_path / name)
    assert run.code == 2 and ".xlsx or .json" in run.err
    assert run.outcome.result is None and not (tmp_path / name).exists()


def test_export_rank_without_export_is_a_usage_error():
    run = cli(SAMPLE, THREE, "--offline", "--export-rank", "2")
    assert run.code == 2 and "--export-rank needs --export" in run.err and run.outcome.result is None


def test_a_rank_above_top_is_a_usage_error(tmp_path):
    run = cli(SAMPLE, THREE, "--offline", "--top", "3", "--export", tmp_path / "o.json", "--export-rank", "4")
    assert run.code == 2 and "--top" in run.err and run.outcome.result is None
    assert not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("rank", ["0", "-1", "two"])
def test_a_rank_that_isnt_a_positive_number_is_a_usage_error(tmp_path, rank):
    run = cli(SAMPLE, THREE, "--offline", "--export", tmp_path / "o.json", "--export-rank", rank)
    assert run.code == 2 and run.outcome.result is None


def test_fewer_schedules_than_the_rank_prints_them_and_writes_nothing(tmp_path):
    path = tmp_path / "o.json"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path, "--export-rank", "5"))
    assert run.code == 1 and len(run.outcome.result.schedules) == 3
    assert "Schedule 3 of 3" in run.out
    assert "Nothing exported: only 3 schedules were found, so there's no schedule 5." in run.out
    assert not path.exists()


def test_no_schedule_found_writes_nothing(tmp_path):
    path = tmp_path / "o.xlsx"
    run = cli(SAMPLE, "fridays off", "--offline", "--export", path)
    assert run.code == 1 and "Nothing exported: no schedule was found." in run.out and not path.exists()


def test_a_file_that_cant_be_written_is_exit_2_after_the_results(tmp_path):
    path = tmp_path / "missing-folder" / "o.json"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path))
    assert run.code == 2 and "Schedule 1 of 3" in run.out and "can't write" in run.err


def test_quitting_the_review_writes_nothing(tmp_path):
    path = tmp_path / "o.json"
    run = cli(SAMPLE, THREE, "--offline", "--review", "--export", path, console=FakeConsole(["q"]))
    assert run.code == 0 and run.outcome.result is None and not path.exists()


def test_export_after_review_edits_holds_the_edited_rules(tmp_path):
    path = tmp_path / "o.json"
    run = checked(cli(SAMPLE, "fridays off", "--offline", "--review", "--export", path, console=FakeConsole(["h 1", ""])))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [parse_constraint(c) for c in data["constraints"]] == run.outcome.constraints
    assert any(c["kind"] == "day_off" and c["strength"] == "soft" for c in data["constraints"])


def test_the_cli_prints_ids_but_the_exported_file_has_full_names(tmp_path):
    path = tmp_path / "o.json"
    run = checked(cli(SAMPLE, THREE, "--offline", "--export", path))
    assert "Courses: ECP, OLD," in run.out
    assert json.loads(path.read_text(encoding="utf-8"))["courses"].startswith("Emergency Command Procedures (ECP), ")
