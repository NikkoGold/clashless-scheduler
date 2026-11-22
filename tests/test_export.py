"""Exports of one schedule (scheduler/export.py): the Excel workbook, the JSON file, the course colours, and the check
that a schedule sent back by the page really belongs to the catalog. Shared by the CLI and the web API.
"""
import io
import json
import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from openpyxl import load_workbook

from scheduler import export
from scheduler.catalog import parse_catalog
from scheduler.models import Catalog, Schedule, format_time, parse_constraint
from scheduler.present import either, rule
from scheduler.solver import solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import assert_valid_results
from tests.helpers.strategies import problems

DOM_JS = Path(__file__).parent.parent / "scheduler" / "web" / "static" / "dom.js"


# ---- helpers ----

def solved(cat: Catalog, constraints, k=5):
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    return result


def workbook(cat, schedule, rank=1, best=None, courses="A, B"):
    return load_workbook(io.BytesIO(export.to_xlsx(cat, schedule, rank, best=best, courses=courses)))


def grid_blocks(ws) -> dict:
    """Read the grid sheet back: {(day, start hour, end hour): (text, fill colour)} for every filled block."""
    days = {}
    for col in range(2, ws.max_column + 1):
        v = ws.cell(export.GRID_HEADER_ROW, col).value
        if v:
            days[col] = v
    hours = {}
    for row in range(export.GRID_HEADER_ROW + 1, ws.max_row + 1):
        v = ws.cell(row, 1).value
        if v:
            hours[row] = int(v[:2])
    spans = {(m.min_row, m.min_col): m.max_row for m in ws.merged_cells.ranges}
    blocks = {}
    for row, hour in hours.items():
        for col, day in days.items():
            cell = ws.cell(row, col)
            if cell.value is None:
                continue
            last = spans.get((row, col), row)
            blocks[(day, hour, hours[last] + 1)] = (cell.value, cell.fill.fgColor.rgb[-6:])
    return blocks


def filled_cells(ws) -> int:
    """Grid cells with any fill, counting a merged block once."""
    inside = {(r, c) for m in ws.merged_cells.ranges for r in range(m.min_row + 1, m.max_row + 1)
              for c in range(m.min_col, m.max_col + 1)}
    return sum(1 for row in ws.iter_rows(min_row=export.GRID_HEADER_ROW + 1, min_col=2) for cell in row
               if cell.fill.fill_type == "solid" and (cell.row, cell.column) not in inside)


def expected_blocks(cat: Catalog, schedule: Schedule) -> dict:
    found = {}
    for ch in schedule.choices:
        colour = export.course_colours(next(i for i, c in enumerate(cat.courses) if c.id == ch.course))["fill"]
        for s in ch.option.sessions:
            found[(s.day, s.start // 60, s.end // 60)] = (ch, colour)
    return found


def two_course_catalog(week=None):
    return catalog(
        course("A", name="Algorithms", lecture=[grp("1", "Mon 10-12", instructor="Cohen", room="B1")],
               lab=[grp("11", "Tue 14-15", room="L1"), grp("12", "Tue 14-15", room="L2")]),
        course("B", name="Biology", lecture=[grp("1", "Wed 9-10", "Thu 16-18", instructor="Levi")]),
        week=week,
    )


# ---- colours ----

@pytest.mark.parametrize("hsl, rgb", [
    ((0, 100, 50), "FF0000"), ((120, 100, 25), "008000"), ((240, 100, 50), "0000FF"),
    ((0, 0, 50), "808080"), ((0, 0, 100), "FFFFFF"), ((60, 100, 50), "FFFF00"),
])
def test_hsl_is_converted_to_the_same_rgb_as_css(hsl, rgb):
    assert export.hsl_to_hex(*hsl) == rgb


def test_the_page_and_the_exports_use_the_same_course_hues():
    found = re.search(r"export const HUES = \[([^\]]*)\]", DOM_JS.read_text(encoding="utf-8"))
    assert found, "HUES not found in dom.js"
    assert [int(x) for x in found.group(1).split(",")] == export.HUES


def test_course_colours_are_the_pages_light_mode_colours():
    hue = export.HUES[3]
    assert export.course_colours(3) == {"fill": export.hsl_to_hex(hue, 80, 91), "border": export.hsl_to_hex(hue, 50, 62),
                                        "text": export.hsl_to_hex(hue, 65, 22)}


def test_course_colours_repeat_after_the_last_hue():
    assert export.course_colours(len(export.HUES)) == export.course_colours(0)


# ---- the Excel grid ----

@pytest.mark.parametrize("week", [None, ["Mon", "Tue", "Wed", "Thu", "Fri"], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]])
def test_the_grid_has_a_column_per_day_of_the_catalogs_week_in_order(week):
    cat = two_course_catalog(week)
    ws = workbook(cat, solved(cat, must("A", "B")).schedules[0])[export.GRID_SHEET]
    heads = [ws.cell(export.GRID_HEADER_ROW, c).value for c in range(2, len(cat.week) + 2)]
    assert heads == list(cat.week) and ws.cell(export.GRID_HEADER_ROW, len(cat.week) + 2).value is None


def test_the_grid_rows_run_from_the_earliest_class_to_the_latest():
    cat = two_course_catalog()
    ws = workbook(cat, solved(cat, must("A", "B")).schedules[0])[export.GRID_SHEET]
    labels = [ws.cell(r, 1).value for r in range(export.GRID_HEADER_ROW + 1, ws.max_row + 1)]
    assert labels == [format_time(h * 60) for h in range(9, 18)]


def test_each_session_fills_its_day_and_hours_and_longer_classes_are_one_merged_cell():
    cat = two_course_catalog()
    s = solved(cat, must("A", "B")).schedules[0]
    blocks = grid_blocks(workbook(cat, s)[export.GRID_SHEET])
    assert set(blocks) == {("Mon", 10, 12), ("Tue", 14, 15), ("Wed", 9, 10), ("Thu", 16, 18)}


def test_a_cell_names_the_course_component_and_group_and_a_merged_option_reads_11_or_12():
    cat = two_course_catalog()
    blocks = grid_blocks(workbook(cat, solved(cat, must("A", "B")).schedules[0])[export.GRID_SHEET])
    assert blocks[("Mon", 10, 12)][0].startswith("A lecture 1")
    assert blocks[("Tue", 14, 15)][0].startswith("A lab 11 or 12")
    assert "B1" in blocks[("Mon", 10, 12)][0] and "L1 or L2" in blocks[("Tue", 14, 15)][0]


def test_each_course_has_its_page_colour_on_every_session():
    cat = two_course_catalog()
    blocks = grid_blocks(workbook(cat, solved(cat, must("A", "B")).schedules[0])[export.GRID_SHEET])
    a, b = export.course_colours(0)["fill"], export.course_colours(1)["fill"]
    assert {k: v[1] for k, v in blocks.items()} == {("Mon", 10, 12): a, ("Tue", 14, 15): a,
                                                     ("Wed", 9, 10): b, ("Thu", 16, 18): b}


def test_the_grid_title_names_the_rank_and_the_term():
    cat = two_course_catalog()
    ws = workbook(cat, solved(cat, must("A", "B")).schedules[0], rank=4)[export.GRID_SHEET]
    assert ws["A1"].value == "Schedule 4 · T"


# ---- the sessions sheet ----

def test_the_sessions_sheet_has_one_row_per_session_in_week_order():
    cat = two_course_catalog()
    ws = workbook(cat, solved(cat, must("A", "B")).schedules[0])[export.SESSIONS_SHEET]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == ["Course", "Name", "Component", "Group", "Day", "Start", "End", "Room", "Instructor"]
    assert rows[1:] == [
        ["A", "Algorithms", "lecture", "1", "Mon", "10:00", "12:00", "B1", "Cohen"],
        ["A", "Algorithms", "lab", "11 or 12", "Tue", "14:00", "15:00", "L1 or L2", None],
        ["B", "Biology", "lecture", "1", "Wed", "09:00", "10:00", None, "Levi"],
        ["B", "Biology", "lecture", "1", "Thu", "16:00", "18:00", None, "Levi"],
    ]


# ---- the rules sheet: the user stays fully informed ----

def test_the_rules_sheet_lists_every_rule_met_or_broken_with_its_cost():
    cat = two_course_catalog()
    rules = must("A", "B") + [parse_constraint({"kind": "no_class_before", "time": "10:00", "strength": "soft",
                                                "weight": 3})]
    s = solved(cat, rules).schedules[0]
    ws = workbook(cat, s)[export.RULES_SHEET]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == ["Rule", "Met", "Cost"]
    assert rows[1:1 + len(s.breakdown)] == [[rule(i.constraint), "met" if i.met else "broken", 0 if i.met else i.weighted]
                                            for i in s.breakdown]
    assert ["broken"] == [r[1] for r in rows[1:1 + len(s.breakdown)] if r[1] == "broken"]


def test_the_rules_sheet_carries_the_register_notes_and_the_differences_from_number_one():
    cat = catalog(course("A", lecture=[grp("1", "Mon 10-12", instructor="Cohen"), grp("2", "Mon 10-12", instructor="Levi"),
                                       grp("3", "Tue 10-12")]))
    rules = must("A") + [parse_constraint({"kind": "avoid_instructor", "name": "Cohen", "strength": "soft", "weight": 2}),
                         parse_constraint({"kind": "avoid_instructor", "name": "Levi", "strength": "soft", "weight": 2}),
                         parse_constraint({"kind": "day_off", "day": "Tue", "strength": "soft", "weight": 5})]
    result = solved(cat, rules)
    best, second = result.schedules[0], result.schedules[1]
    notes = [ch.register_note for ch in best.choices if ch.register_note]
    assert notes, "the scenario should produce a register note"
    text = [c for r in workbook(cat, best)[export.RULES_SHEET].iter_rows(values_only=True) for c in r if c]
    assert all(n in text for n in notes)
    text2 = [c for r in workbook(cat, second, rank=2, best=best)[export.RULES_SHEET].iter_rows(values_only=True)
             for c in r if c]
    assert "Differs from schedule 1" in text2


# ---- any rank, not only #1 ----

def test_exporting_rank_three_gives_the_third_schedule():
    cat = catalog(course("A", lecture=[grp(str(i), f"Mon {8 + i}-{9 + i}") for i in range(1, 6)]))
    result = solved(cat, must("A"))
    third = result.schedules[2]
    blocks = grid_blocks(workbook(cat, third, rank=3, best=result.schedules[0])[export.GRID_SHEET])
    assert set(blocks) == {(s.day, s.start // 60, s.end // 60) for s in third.choices[0].option.sessions}
    assert export.to_json(cat, third, 3, must("A"))["schedule"] == third.model_dump(mode="json")


# ---- generated: the grid holds exactly the schedule ----

@settings(max_examples=40, deadline=None)
@given(problems())
def test_the_grid_holds_exactly_the_schedules_sessions_for_random_requests(problem):
    cat, constraints = problem
    result = solve(cat, constraints, k=3)
    for rank, s in enumerate(result.schedules, 1):
        ws = workbook(cat, s, rank=rank, best=result.schedules[0])[export.GRID_SHEET]
        blocks, want = grid_blocks(ws), expected_blocks(cat, s)
        assert set(blocks) == set(want)
        for key, (ch, colour) in want.items():
            text, fill = blocks[key]
            assert text.startswith(f"{ch.course} {ch.component} {either(ch.option.group_ids)}") and fill == colour
        assert filled_cells(ws) == len(want)


@settings(max_examples=40, deadline=None)
@given(problems())
def test_the_json_file_loads_back_to_the_same_schedule_rules_and_catalog_for_random_requests(problem):
    cat, constraints = problem
    result = solve(cat, constraints, k=3)
    for rank, s in enumerate(result.schedules, 1):
        data = json.loads(json.dumps(export.to_json(cat, s, rank, constraints, best=result.schedules[0], courses="x")))
        assert data["format"] == "clashless-schedule" and data["version"] == 1 and data["rank"] == rank
        assert Schedule.model_validate(data["schedule"]) == s
        assert [parse_constraint(c) for c in data["constraints"]] == constraints
        assert parse_catalog(data["catalog"]) == cat
        assert data["term"] == cat.term and data["courses"] == "x"
        assert len(data["view"]["rules"]) == len(s.breakdown)


# ---- a schedule sent back must belong to the catalog ----

def _tampered(cat, change):
    s = solved(cat, must("A", "B")).schedules[0].model_dump(mode="json")
    change(s)
    return Schedule.model_validate(s)


@pytest.mark.parametrize("change, why", [
    (lambda s: s["choices"][0].update(course="ZZZ"), "ZZZ"),
    (lambda s: s["choices"][0].update(component="seminar"), "seminar"),
    (lambda s: s["choices"][0]["option"]["groups"][0].update(group="99"), "99"),
    (lambda s: s["choices"][0]["option"]["groups"][0]["sessions"][0].update(start="08:00"), "doesn't match"),
])
def test_a_schedule_that_isnt_from_the_catalog_is_refused(change, why):
    cat = two_course_catalog()
    with pytest.raises(export.ExportError, match=why):
        export.check_schedule(cat, _tampered(cat, change))


def test_a_schedule_from_the_catalog_passes_the_check():
    cat = two_course_catalog()
    for s in solved(cat, must("A", "B")).schedules:
        export.check_schedule(cat, s)


# ---- writing a file (the CLI's --export) ----

@pytest.mark.parametrize("name", ["out.xlsx", "out.json", "OUT.XLSX"])
def test_the_format_is_taken_from_the_extension(tmp_path, name):
    cat = two_course_catalog()
    s = solved(cat, must("A", "B")).schedules[0]
    path = tmp_path / name
    export.write(path, cat, s, 1, must("A", "B"), courses="A, B")
    if name.lower().endswith(".json"):
        assert json.loads(path.read_text(encoding="utf-8"))["rank"] == 1
    else:
        assert load_workbook(path).sheetnames == [export.GRID_SHEET, export.SESSIONS_SHEET, export.RULES_SHEET]


@pytest.mark.parametrize("name", ["out.pdf", "out.csv", "out", "out.xlsx.bak"])
def test_other_extensions_are_refused(tmp_path, name):
    with pytest.raises(export.ExportError, match=r"\.xlsx or \.json"):
        export.format_of(tmp_path / name)
