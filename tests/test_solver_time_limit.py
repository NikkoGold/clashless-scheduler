"""The 3-minute time limit: whatever was proven before the limit is kept, and nothing unproven is claimed.

A fake clock makes the limit hit at an exact stage, so these tests are fast and deterministic.
"""
import random

import pytest
from ortools.sat.python import cp_model

import scheduler.solver as solver_module
from scheduler.models import DayOff, MustTake, TakeNOf
from scheduler.solver import TIME_LIMIT_SECONDS, solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import assert_valid_results


class StepClock:
    """Returns the given times in order, then the last one forever."""

    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


LATE = 10 * TIME_LIMIT_SECONDS


def five_choices():
    # A alone, with 5 lecture groups: exactly 5 schedules
    return catalog(course("A", lecture=[grp(str(i), f"Sun {8 + 2 * i}-{9 + 2 * i}") for i in range(5)]))


def test_the_limit_is_three_minutes():
    assert TIME_LIMIT_SECONDS == 180


def test_hitting_the_limit_before_the_first_result_claims_nothing(monkeypatch):
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, LATE))
    result = solve(five_choices(), must("A"), k=5)
    assert result.schedules == []
    assert result.timed_out
    assert result.conflict == [] and not result.nothing_to_schedule
    assert "3 minutes" in result.note and "no schedule works" not in result.note.lower()


def test_hitting_the_limit_after_two_results_keeps_them(monkeypatch):
    # start, first solve, second solve, then the third solve finds the time is up
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, 0, 0, LATE))
    cat, constraints = five_choices(), must("A")
    result = solve(cat, constraints, k=5)
    assert len(result.schedules) == 2
    assert result.timed_out
    assert "3 minutes" in result.note and "2 found" in result.note and "may be more" in result.note
    assert_valid_results(result, cat, constraints, 5)
    # they're the same two, in the same order, as without a limit: each was proven best in turn
    unlimited = solve(cat, constraints, k=5)
    assert [s.penalty for s in result.schedules] == [s.penalty for s in unlimited.schedules[:2]]


def test_hitting_the_limit_while_finding_the_conflict_names_no_rules(monkeypatch):
    friday_only = catalog(course("A", lecture=[grp("1", "Fri 10-12")]))
    # start, the solve that proves there's no schedule, then the conflict search runs out of time
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, 0, LATE))
    result = solve(friday_only, must("A") + [DayOff(day="Fri")], k=5)
    assert result.schedules == [] and result.conflict == []
    assert result.timed_out
    assert "no schedule" in result.note.lower() and "3 minutes" in result.note and "conflict" in result.note


def test_a_solve_cut_short_by_the_limit_counts_as_timed_out_not_as_an_error(monkeypatch):
    class Unfinished:
        parameters = cp_model.CpSolver().parameters

        def solve(self, model):
            return cp_model.UNKNOWN

    monkeypatch.setattr(solver_module, "_solver", Unfinished)
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, 0, LATE))  # time is up when it returns
    result = solve(five_choices(), must("A"), k=5)
    assert result.timed_out and result.schedules == []


def test_an_unfinished_solve_with_time_left_is_still_an_error(monkeypatch):
    class Unfinished:
        parameters = cp_model.CpSolver().parameters

        def solve(self, model):
            return cp_model.UNKNOWN

    monkeypatch.setattr(solver_module, "_solver", Unfinished)
    with pytest.raises(RuntimeError):
        solve(five_choices(), must("A"), k=5)


def test_a_normal_request_does_not_time_out():
    result = solve(five_choices(), must("A"), k=5)
    assert len(result.schedules) == 5 and not result.timed_out and result.note is None


@pytest.mark.slow
def test_a_realistic_large_request_finishes_within_the_limit():
    # 10 courses wanted (6 required + pick 4 of 8) from a 40-course catalog, 20 results
    rng = random.Random(0)
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]

    def group(i):
        h = rng.randint(8, 18)
        return grp(str(i), *[f"{d} {h}-{h + rng.choice([1, 2])}" for d in rng.sample(days, rng.choice([1, 2]))])

    cat = catalog(*[
        course(f"C{n}", lecture=[group(i) for i in range(1, 4)], lab=[group(i) for i in range(11, 15)],
               tutorial=[group(i) for i in range(21, 24)])
        for n in range(40)
    ])
    ids = [c.id for c in cat.courses]
    constraints = [MustTake(course=c) for c in ids[:6]] + [TakeNOf(courses=ids[6:14], n=4, strength="soft")]
    result = solve(cat, constraints, k=20)
    assert not result.timed_out
    assert_valid_results(result, cat, constraints, 20)
