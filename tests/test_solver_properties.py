"""Random small catalogs, checked against the invariant checker and the brute-force oracle.

When one of these fails: find the cause, copy the shrunk catalog into a named regression test in
the relevant test_solver_*.py file, then fix the cause. See test-core-logic/property-tests.md.
"""
import pytest
from hypothesis import assume, event, given, settings
from hypothesis import strategies as st

from scheduler.solver import solve
from tests.helpers.invariants import assert_valid_results, selectable_courses
from tests.helpers.oracle import OracleTooLarge, assert_matches_oracle, enumerate_schedules
from tests.helpers.strategies import ALL_KINDS, problems

pytestmark = pytest.mark.slow


@given(problem=problems(), k=st.integers(1, 5))
def test_results_always_pass_the_invariant_checker(problem, k):
    cat, constraints = problem
    assert_valid_results(solve(cat, constraints, k=k), cat, constraints, k)


@given(problem=problems(), k=st.integers(1, 5))
def test_results_always_match_the_brute_force_oracle(problem, k):
    cat, constraints = problem
    result = solve(cat, constraints, k=k)
    try:
        assert_matches_oracle(result, cat, constraints, k)
    except OracleTooLarge:
        assume(False)


@pytest.mark.parametrize("kind", ALL_KINDS)
@settings(max_examples=40)
@given(data=st.data(), k=st.integers(1, 5))
def test_every_constraint_kind_matches_the_checker_and_the_oracle(kind, data, k):
    # a uniform draw rarely produces a given kind, so each one gets its own run
    cat, constraints = data.draw(problems(kind=kind))
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    try:
        assert_matches_oracle(result, cat, constraints, k)
    except OracleTooLarge:
        assume(False)


@given(problem=problems(), k=st.integers(1, 5))
def test_solving_twice_gives_the_same_scores(problem, k):
    cat, constraints = problem
    first = [s.penalty for s in solve(cat, constraints, k=k).schedules]
    second = [s.penalty for s in solve(cat, constraints, k=k).schedules]
    assert first == second


def _check_conflict(cat, constraints):
    """A reported conflict really conflicts, and every part of it is needed, judged by the oracle.

    Feasibility is judged the way the conflict is meant: the full request's courses stay available
    (dropping a must_take drops the requirement, not the course), and "at least one course" only
    counts when the result says it's part of the conflict (`nothing_to_schedule`).
    """
    result = solve(cat, constraints, k=1)
    event("unsolvable" if not result.schedules else "solvable")
    if result.schedules:
        assert result.conflict == []
        assert not result.nothing_to_schedule
        return

    assert all(c.strength == "hard" for c in result.conflict), "a soft rule can't be part of a conflict"
    assert result.conflict or result.nothing_to_schedule, "nothing fits, but nothing is blamed"
    courses = selectable_courses(constraints)
    soft = [c for c in constraints if c.strength == "soft"]

    def feasible(cs, allow_empty=not result.nothing_to_schedule):
        try:
            return bool(enumerate_schedules(cat, cs, candidates=courses, allow_empty=allow_empty))
        except OracleTooLarge:
            assume(False)

    assert not feasible(result.conflict + soft), "the named rules don't actually conflict"
    for i, dropped in enumerate(result.conflict):
        assert feasible(result.conflict[:i] + result.conflict[i + 1:] + soft), f"{dropped} isn't needed"
    if result.nothing_to_schedule:
        assert feasible(result.conflict + soft, allow_empty=True), "'nothing to schedule' isn't needed"


@given(problem=problems())
def test_a_reported_conflict_really_conflicts_and_every_part_is_needed(problem):
    _check_conflict(*problem)


@pytest.mark.parametrize("kind", ALL_KINDS)
@settings(max_examples=40)
@given(data=st.data())
def test_conflicts_involving_every_constraint_kind_are_minimal(kind, data):
    _check_conflict(*data.draw(problems(kind=kind)))
