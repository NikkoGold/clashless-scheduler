"""Smoke tests for data/sample_courses.json. Never assert exact results here (see data/README.md)."""
from pathlib import Path

from scheduler.catalog import load_catalog
from scheduler.constraints import default_constraints
from scheduler.models import Compact, DayOff, FreeBlock, MaxGap, NoClassBefore, PreferInstructor
from scheduler.solver import solve
from tests.helpers.invariants import assert_valid_results

SAMPLE = Path(__file__).parent.parent / "data" / "sample_courses.json"


def test_sample_catalog_loads():
    cat = load_catalog(SAMPLE)
    assert len(cat.courses) == 16
    assert sum(c.required for c in cat.courses) == 8


def test_sample_required_courses_can_all_be_scheduled():
    cat = load_catalog(SAMPLE)
    constraints = default_constraints(cat)
    result = solve(cat, constraints, k=5)
    assert_valid_results(result, cat, constraints, k=5)  # too big for the oracle
    assert len(result.schedules) == 5


def test_sample_solves_with_a_mix_of_hard_and_soft_constraints():
    cat = load_catalog(SAMPLE)
    constraints = default_constraints(cat) + [
        NoClassBefore(time="09:00"),
        PreferInstructor(name="Cohen", strength="soft", weight=7),
        DayOff(day="Thu", strength="soft", weight=4),
        FreeBlock(start="12:00", end="13:00", strength="soft", weight=3),
        MaxGap(minutes=120, strength="soft", weight=2),
        Compact(weight=1),
    ]
    result = solve(cat, constraints, k=5)
    assert_valid_results(result, cat, constraints, k=5)  # too big for the oracle
    assert result.schedules
