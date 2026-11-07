"""Random small catalogs, checked against the invariant checker and the brute-force oracle.

When one of these fails: find the cause, copy the shrunk catalog into a named regression test in
the relevant test_solver_*.py file, then fix the cause. See test-core-logic/property-tests.md.
"""
import pytest
from hypothesis import assume, event, given, settings
from hypothesis import strategies as st

from scheduler.solver import solve
from tests.helpers.invariants import assert_valid_results
from tests.helpers.oracle import OracleTooLarge, assert_matches_oracle
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


def _feasible(cat, constraints) -> bool:
    try:
        return bool(solve(cat, constraints, k=1).schedules)
    except ValueError as e:
        assert "no courses" in str(e)
        return True  # nothing is being asked for, so nothing conflicts


@given(problem=problems())
def test_a_reported_conflict_really_conflicts_and_every_part_is_needed(problem):
    cat, constraints = problem
    result = solve(cat, constraints, k=1)
    event("unsolvable" if not result.schedules else "solvable")
    if result.schedules:
        assert result.conflict == []
        return

    assert all(c.strength == "hard" for c in result.conflict), "a soft rule can't be part of a conflict"
    rest = [c for c in constraints if c.strength == "soft"]
    assert not _feasible(cat, result.conflict + rest)
    for i in range(len(result.conflict)):
        assert _feasible(cat, result.conflict[:i] + result.conflict[i + 1 :] + rest)
