"""Brute-force oracle: tries every combination to say what the solver *should* return.

Merging is re-implemented here rather than imported from scheduler.catalog, so the
oracle doesn't share that code with the solver. See .claude/skills/test-core-logic/oracle.md.
"""
import itertools
import math
from types import SimpleNamespace

from tests.helpers.invariants import (
    MEMBER_KINDS,
    assert_valid_results,
    clashes,
    hard_member_allows,
    member_penalty,
    rule_for,
    schedule_key,
    selectable_courses,
)

MAX_COMBINATIONS = 5_000


class OracleTooLarge(Exception):
    pass


def _merged(groups) -> list[list]:
    """Groups with identical session sets, merged into lists of members."""
    by_times: dict[frozenset, list] = {}
    for g in groups:
        times = frozenset((s.day, s.start, s.end) for s in g.sessions)
        by_times.setdefault(times, []).append(g)
    return list(by_times.values())


def _best_member(course_id, component, members, constraints, catalog):
    """A merged option is scored by its best member; ties go to the first listed."""
    soft = [c for c in constraints if c.strength == "soft" and c.kind in MEMBER_KINDS]
    return min(
        members,
        key=lambda g: sum(c.weight * member_penalty(c, course_id, component, g, catalog) for c in soft),
    )


def _choice(course_id, component, members, constraints, catalog) -> SimpleNamespace:
    # duck-types the solver's Choice, so the invariant rules work on both
    best = _best_member(course_id, component, members, constraints, catalog)
    return SimpleNamespace(
        course=course_id,
        component=component,
        register_for=[best.group],
        option=SimpleNamespace(
            group_ids=tuple(g.group for g in members),
            groups=members,
            sessions=members[0].sessions,
        ),
    )


def _component_options(course, constraints, catalog) -> dict[str, list[list]]:
    """Per component, the merged options left once hard member rules have removed groups."""
    hard = [c for c in constraints if c.strength == "hard" and c.kind in MEMBER_KINDS]
    options = {}
    for component, groups in course.components.items():
        left = [g for g in groups if all(hard_member_allows(c, course.id, component, g, catalog) for c in hard)]
        options[component] = _merged(left)
    return options


def enumerate_schedules(catalog, constraints) -> list[SimpleNamespace]:
    courses = {c.id: c for c in catalog.courses}
    candidates = sorted(selectable_courses(constraints))

    # every non-empty subset of the candidate courses is a possible selection
    selections = []
    total = 0
    for size in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            slots = [
                [(cid, comp, members) for members in options]
                for cid in subset
                for comp, options in _component_options(courses[cid], constraints, catalog).items()
            ]
            total += math.prod(len(s) for s in slots)
            if total > MAX_COMBINATIONS:
                raise OracleTooLarge(f"more than {MAX_COMBINATIONS} combinations")
            selections.append(slots)

    found = []
    for slots in selections:
        for combo in itertools.product(*slots):
            sessions = [s for *_, members in combo for s in members[0].sessions]
            if any(clashes(a, b) for a, b in itertools.combinations(sessions, 2)):
                continue
            schedule = SimpleNamespace(choices=[_choice(*c, constraints, catalog) for c in combo])
            if all(rule_for(c.kind).hard_ok(schedule, c, catalog) for c in constraints if c.strength == "hard"):
                found.append(schedule)
    return found


def score(schedule, constraints, catalog) -> int:
    """Total weighted soft penalty."""
    return sum(
        c.weight * rule_for(c.kind).soft_penalty(schedule, c, catalog) for c in constraints if c.strength == "soft"
    )


def assert_matches_oracle(result, catalog, constraints, k):
    expected = enumerate_schedules(catalog, constraints)
    got = result.schedules

    assert bool(got) == bool(expected), f"solver found {len(got)} schedules, oracle found {len(expected)}"
    assert len(got) == min(k, len(expected)), f"expected {min(k, len(expected))} results, got {len(got)}"

    expected_keys = {schedule_key(s) for s in expected}
    for s in got:
        assert schedule_key(s) in expected_keys, "solver returned a schedule the oracle says is invalid"

    if got:
        # compare score multisets, not positions: tied schedules can come back in any order
        expected_scores = sorted(score(s, constraints, catalog) for s in expected)
        assert sorted(score(s, constraints, catalog) for s in got) == expected_scores[: len(got)]


def checked_solve(cat, constraints, k=5):
    """Solve, then run the result through the invariant checker and the oracle."""
    from scheduler.solver import solve

    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    assert_matches_oracle(result, cat, constraints, k)
    return result
