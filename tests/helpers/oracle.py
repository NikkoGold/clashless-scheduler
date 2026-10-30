"""Brute-force oracle: tries every combination to say what the solver *should* return.

Merging is re-implemented here rather than imported from scheduler.catalog, so the
oracle doesn't share that code with the solver. See .claude/skills/test-core-logic/oracle.md.
"""
import itertools
import math
from types import SimpleNamespace

from tests.helpers.invariants import clashes, rule_for, schedule_key, selectable_courses

MAX_COMBINATIONS = 5_000


class OracleTooLarge(Exception):
    pass


def _merged(groups) -> list[tuple[frozenset, list]]:
    """[(member group ids, sessions)] with identical session sets merged."""
    by_times: dict[frozenset, list] = {}
    for g in groups:
        times = frozenset((s.day, s.start, s.end) for s in g.sessions)
        by_times.setdefault(times, []).append(g)
    return [(frozenset(g.group for g in members), members[0].sessions) for members in by_times.values()]


def _choice(course, component, ids, sessions) -> SimpleNamespace:
    # duck-types the solver's Choice, so the invariant rules work on both
    return SimpleNamespace(course=course, component=component,
                           option=SimpleNamespace(group_ids=ids, sessions=sessions))


def enumerate_schedules(catalog, constraints) -> list[SimpleNamespace]:
    courses = {c.id: c for c in catalog.courses}
    selected = sorted(selectable_courses(constraints))  # step 2: also every take_n_of selection
    slots = [
        [(cid, comp, ids, sessions) for ids, sessions in _merged(groups)]
        for cid in selected
        for comp, groups in courses[cid].components.items()
    ]
    total = math.prod(len(s) for s in slots)
    if total > MAX_COMBINATIONS:
        raise OracleTooLarge(f"{total} combinations")

    found = []
    for combo in itertools.product(*slots):
        sessions = [s for *_, ss in combo for s in ss]
        if any(clashes(a, b) for a, b in itertools.combinations(sessions, 2)):
            continue
        schedule = SimpleNamespace(choices=[_choice(*c) for c in combo])
        if all(rule_for(c.kind).hard_ok(schedule, c) for c in constraints if c.strength == "hard"):
            found.append(schedule)
    return found


def score(schedule, constraints) -> int:
    """Total weighted soft penalty (always 0 in step 1, which has no soft constraints)."""
    return sum(c.weight * rule_for(c.kind).soft_penalty(schedule, c) for c in constraints if c.strength == "soft")


def assert_matches_oracle(result, catalog, constraints, k):
    expected = enumerate_schedules(catalog, constraints)
    got = result.schedules

    assert bool(got) == bool(expected), f"solver found {len(got)} schedules, oracle found {len(expected)}"
    assert len(got) == min(k, len(expected)), f"expected {min(k, len(expected))} results, got {len(got)}"

    expected_keys = {schedule_key(s) for s in expected}
    for s in got:
        assert schedule_key(s) in expected_keys, "solver returned a schedule the oracle says is invalid"

    if got:
        expected_scores = sorted(score(s, constraints) for s in expected)
        assert sorted(score(s, constraints) for s in got) == expected_scores[: len(got)]
