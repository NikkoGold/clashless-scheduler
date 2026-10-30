"""Invariant checker that every solver test runs its results through.

Rules are re-derived from the chosen sessions here, never taken from the solver.
See .claude/skills/test-core-logic/invariants.md.
"""
import itertools
import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Rule:
    hard_ok: Callable  # (schedule, constraint) -> bool
    soft_penalty: Callable  # (schedule, constraint) -> int, unweighted


def scheduled_courses(schedule) -> set[str]:
    return {ch.course for ch in schedule.choices}


RULES: dict[str, Rule] = {
    "must_take": Rule(
        hard_ok=lambda s, c: c.course in scheduled_courses(s),
        soft_penalty=lambda s, c: 0 if c.course in scheduled_courses(s) else 1,
    ),
}


def rule_for(kind: str) -> Rule:
    if kind not in RULES:
        raise NotImplementedError(f"no invariant rule for constraint kind {kind!r}; add it to RULES")
    return RULES[kind]


def selectable_courses(constraints) -> set[str]:
    """Courses the solver may schedule: must_take courses (take_n_of pools join in step 2)."""
    return {c.course for c in constraints if c.kind == "must_take"}


def clashes(a, b) -> bool:
    return a.day == b.day and a.start < b.end and b.start < a.end


def schedule_key(schedule) -> frozenset:
    """Identity on the week grid: (course, component, merged option)."""
    return frozenset((ch.course, ch.component, frozenset(ch.option.group_ids)) for ch in schedule.choices)


def assert_valid_schedule(schedule, catalog, constraints):
    courses = {c.id: c for c in catalog.courses}

    # completeness: one option per component of each scheduled course, nothing else
    seen = [(ch.course, ch.component) for ch in schedule.choices]
    assert len(seen) == len(set(seen)), f"a component was chosen twice: {seen}"
    allowed = selectable_courses(constraints)
    for course_id in scheduled_courses(schedule):
        assert course_id in allowed, f"{course_id} was scheduled but never selected"
        expected = set(courses[course_id].components)
        got = {comp for c, comp in seen if c == course_id}
        assert got == expected, f"{course_id}: chose components {got}, expected {expected}"

    # every member of an option is a real group with the option's sessions
    sessions = []
    for ch in schedule.choices:
        groups = {g.group: g for g in courses[ch.course].components[ch.component]}
        option_times = sorted((s.day, s.start, s.end) for s in ch.option.sessions)
        for gid in ch.option.group_ids:
            assert gid in groups, f"{ch.course} {ch.component} has no group {gid}"
            member_times = sorted((s.day, s.start, s.end) for s in groups[gid].sessions)
            assert member_times == option_times, f"{ch.course} {ch.component} {gid} doesn't meet at the option's times"
        sessions += [(ch, s) for s in ch.option.sessions]

    # no overlap (back-to-back is fine)
    for (ca, a), (cb, b) in itertools.combinations(sessions, 2):
        assert not clashes(a, b), (
            f"clash: {ca.course} {ca.component} {ca.option.group_ids} and "
            f"{cb.course} {cb.component} {cb.option.group_ids} on {a.day}"
        )

    # hard constraints
    for c in constraints:
        if c.strength == "hard":
            assert rule_for(c.kind).hard_ok(schedule, c), f"hard constraint broken: {c}"

    # score breakdown is checked here from step 2, once scoring exists


def assert_valid_results(result, catalog, constraints, k):
    schedules = result.schedules
    assert isinstance(schedules, list)
    assert len(schedules) <= k
    for s in schedules:
        assert_valid_schedule(s, catalog, constraints)

    keys = [schedule_key(s) for s in schedules]
    assert len(keys) == len(set(keys)), "two results look identical on the grid"

    # scores non-improving by rank: checked here from step 2, once scoring exists

    if 0 < len(schedules) < k:
        assert result.note, f"only {len(schedules)} results for k={k} but no note"
        match = re.search(r"\bonly (\d+)\b", result.note, re.IGNORECASE)
        assert match and int(match.group(1)) == len(schedules), f"note doesn't say 'only {len(schedules)}': {result.note!r}"
    elif len(schedules) == k:
        assert result.note is None, f"k results found but there's a note: {result.note!r}"
