"""The export routes: the page sends the open schedule (any rank) with its catalog, and gets an Excel or JSON file."""
import io

import pytest
from openpyxl import load_workbook

from scheduler import export
from scheduler.catalog import parse_catalog
from scheduler.models import Schedule
from tests.helpers.api_client import api, ok, parse, sample, solve
from tests.helpers.catalog_builder import catalog, course, grp

pytestmark = pytest.mark.usefixtures("fresh_settings")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def c():
    return api()


def body_for(j: dict, cat: dict, rank: int) -> dict:
    """What the page sends for the schedule open at `rank` (1-based), from a /api/solve answer."""
    return {"catalog": cat, "schedule": j["result"]["schedules"][rank - 1], "best": j["result"]["schedules"][0],
            "constraints": j["constraints"], "courses": j["courses"], "rank": rank}


@pytest.fixture
def solved(c):
    cat = sample()
    j = solve(c, cat, parse(c, cat, "no classes before 10, keep 12-13 free")["rules"])
    assert len(j["schedules"]) == 3
    return cat, j


@pytest.mark.parametrize("rank", [1, 2, 3])
def test_the_xlsx_route_returns_a_workbook_of_the_schedule_that_was_sent(c, solved, rank):
    cat, j = solved
    r = c.post("/api/export/xlsx", json=body_for(j, cat, rank))
    assert r.status_code == 200 and r.headers["content-type"] == XLSX
    assert f'filename="schedule-{rank}.xlsx"' in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb[export.GRID_SHEET]["A1"].value == f"Schedule {rank} · {cat['term']}"
    sessions = list(wb[export.SESSIONS_SHEET].iter_rows(min_row=2, values_only=True))
    assert len(sessions) == len(j["schedules"][rank - 1]["sessions"])
    assert len(list(wb[export.RULES_SHEET].iter_rows(min_row=2, max_row=1 + len(j["constraints"])))) == len(j["constraints"])


@pytest.mark.parametrize("rank", [1, 3])
def test_the_json_route_returns_the_schedule_that_was_sent(c, solved, rank):
    cat, j = solved
    r = c.post("/api/export/json", json=body_for(j, cat, rank))
    data = ok(r)
    assert f'filename="schedule-{rank}.json"' in r.headers["content-disposition"]
    assert data["rank"] == rank and data["courses"] == j["courses"]
    assert Schedule.model_validate(data["schedule"]) == Schedule.model_validate(j["result"]["schedules"][rank - 1])
    assert data["constraints"] == j["constraints"] and parse_catalog(data["catalog"]) == parse_catalog(cat)


def test_best_is_optional_and_then_nothing_differs(c, solved):
    cat, j = solved
    body = body_for(j, cat, 2)
    del body["best"]
    assert ok(c.post("/api/export/json", json=body))["view"]["differs"] == []


@pytest.mark.parametrize("route", ["/api/export/xlsx", "/api/export/json"])
def test_a_schedule_that_isnt_from_the_catalog_is_an_error(c, solved, route):
    cat, j = solved
    body = body_for(j, cat, 1)
    body["schedule"]["choices"][0]["course"] = "NOPE"
    r = c.post(route, json=body)
    assert r.status_code == 400 and "NOPE" in r.json()["error"]


@pytest.mark.parametrize("route", ["/api/export/xlsx", "/api/export/json"])
@pytest.mark.parametrize("change", [
    lambda b: b.update(rank=0),
    lambda b: b.pop("schedule"),
    lambda b: b.update(schedule={"choices": "x"}),
    lambda b: b.update(catalog={"term": "T"}),
    lambda b: b.update(constraints=[{"kind": "no_such_rule"}]),
])
def test_bad_bodies_are_a_4xx_with_an_error(c, solved, route, change):
    cat, j = solved
    body = body_for(j, cat, 1)
    change(body)
    r = c.post(route, json=body)
    assert 400 <= r.status_code < 500 and r.json()["error"]


def test_a_merged_option_is_exported_as_11_or_12_through_the_api(c):
    cat = catalog(course("A", required=True, lecture=[grp("1", "Mon 10-12")],
                         lab=[grp("11", "Tue 14-15"), grp("12", "Tue 14-15")]), raw=True)
    j = solve(c, cat)
    wb = load_workbook(io.BytesIO(c.post("/api/export/xlsx", json=body_for(j, cat, 1)).content))
    groups = [r[3] for r in wb[export.SESSIONS_SHEET].iter_rows(min_row=2, values_only=True)]
    assert groups == ["1", "11 or 12"]
