"""Invariant checker that every solver test runs its results through.

Rules are re-derived from the chosen sessions here, never taken from the solver.
See .claude/skills/test-core-logic/invariants.md.
"""
import itertools
import re
from dataclasses import dataclass
from typing import Callable

CREDIT_SCALE = 100  # credits may be fractional, so compare them as whole hundredths
POOL_OVER_PENALTY = 2  # a course over a soft take_n_of costs this much; a course short costs 1


@dataclass(frozen=True)
class Rule:
    hard_ok: Callable  # (schedule, constraint, catalog) -> bool
    soft_penalty: Callable  # (schedule, constraint, catalog) -> int, unweighted


# ---- reading a schedule ----

def scheduled_courses(schedule) -> set[str]:
    return {ch.course for ch in schedule.choices}


def courses_of(schedule) -> set[str]:
    return scheduled_courses(schedule)


def groups_of(schedule, course_id, component) -> tuple[str, ...]:
    """The member group ids of the option chosen for this course component."""
    return next(ch.option.group_ids for ch in schedule.choices if (ch.course, ch.component) == (course_id, component))


def choice_of(schedule, course_id, component):
    return next(ch for ch in schedule.choices if (ch.course, ch.component) == (course_id, component))


def entry_for(schedule, constraint):
    """The breakdown entry for this constraint."""
    return next(item for item in schedule.breakdown if item.constraint == constraint)


def _sessions(schedule, days=None) -> list:
    found = [s for ch in schedule.choices for s in ch.option.sessions]
    return [s for s in found if days is None or s.day in days]


def _by_day(schedule) -> dict[str, list]:
    days: dict[str, list] = {}
    for s in _sessions(schedule):
        days.setdefault(s.day, []).append(s)
    return days


def _gaps_in_hours(schedule) -> list[int]:
    gaps = []
    for sessions in _by_day(schedule).values():
        ordered = sorted(sessions, key=lambda s: s.start)
        gaps += [(b.start - a.end) // 60 for a, b in zip(ordered, ordered[1:]) if b.start > a.end]
    return gaps


def _hours_up(minutes: int) -> int:
    return (minutes + 59) // 60


def _credits(schedule, catalog) -> int:
    by_id = {c.id: c for c in catalog.courses}
    return sum(round(by_id[cid].credits * CREDIT_SCALE) for cid in scheduled_courses(schedule))


def _credits_up(units: int) -> int:
    return (units + CREDIT_SCALE - 1) // CREDIT_SCALE


def _registered(ch):
    """The group the student registers for: the first listed by the result."""
    wanted = ch.register_for[0]
    return next(g for g in ch.option.groups if g.group == wanted)


# ---- rules that depend on which member of a merged option you register for ----

MEMBER_KINDS = {"prefer_group", "avoid_group", "prefer_instructor", "avoid_instructor"}


def _teaches(name, course_id, component, catalog) -> bool:
    course = next(c for c in catalog.courses if c.id == course_id)
    return any(g.instructor == name for g in course.components[component])


def _require_course_id(course_id) -> None:
    # The solver's member_penalty takes the Course object, but these take its id. Mixing them up
    # crashes instructor rules and silently zeroes group rules, so catch it here instead.
    if not isinstance(course_id, str):
        raise TypeError(f"expected a course id (str), got {type(course_id).__name__}")


def member_penalty(c, course_id, component, group, catalog) -> int:
    """Unweighted penalty for registering for `group`."""
    _require_course_id(course_id)
    if c.kind == "prefer_group":
        return int((course_id, component) == (c.course, c.component) and group.group != c.group)
    if c.kind == "avoid_group":
        return int((course_id, component) == (c.course, c.component) and group.group == c.group)
    if c.kind == "prefer_instructor":
        return int(_teaches(c.name, course_id, component, catalog) and group.instructor != c.name)
    if c.kind == "avoid_instructor":
        return int(group.instructor == c.name)
    raise NotImplementedError(c.kind)


def hard_member_allows(c, course_id, component, group, catalog) -> bool:
    """Whether a hard member rule leaves this group registrable."""
    _require_course_id(course_id)
    if c.kind == "prefer_group":
        return (course_id, component) != (c.course, c.component) or group.group == c.group
    if c.kind == "avoid_group":
        return (course_id, component) != (c.course, c.component) or group.group != c.group
    if c.kind == "prefer_instructor":
        return not _teaches(c.name, course_id, component, catalog) or group.instructor == c.name
    if c.kind == "avoid_instructor":
        return group.instructor != c.name
    raise NotImplementedError(c.kind)


def _member_hard_ok(s, c, cat) -> bool:
    return all(hard_member_allows(c, ch.course, ch.component, g, cat) for ch in s.choices for g in ch.option.groups)


def _member_soft(s, c, cat) -> int:
    return sum(member_penalty(c, ch.course, ch.component, _registered(ch), cat) for ch in s.choices)


# ---- per-kind definitions ----

def _pool_count(s, c) -> int:
    return len(set(c.courses) & scheduled_courses(s))


def _minutes_before(session, time) -> int:
    return max(0, min(session.end, time) - session.start)


def _minutes_after(session, time) -> int:
    return max(0, session.end - max(session.start, time))


def _block_days(c):
    return None if c.day is None else [c.day]


def _block_overlap(session, c) -> int:
    return max(0, min(session.end, c.end) - max(session.start, c.start))


def _compact_hard(s, c, cat):
    raise AssertionError("compact can't be hard; the model should have rejected it")


RULES: dict[str, Rule] = {
    "must_take": Rule(
        hard_ok=lambda s, c, cat: c.course in scheduled_courses(s),
        soft_penalty=lambda s, c, cat: 0 if c.course in scheduled_courses(s) else 1,
    ),
    "exclude_course": Rule(
        hard_ok=lambda s, c, cat: c.course not in scheduled_courses(s),
        soft_penalty=lambda s, c, cat: 1 if c.course in scheduled_courses(s) else 0,
    ),
    "take_n_of": Rule(
        hard_ok=lambda s, c, cat: _pool_count(s, c) == c.n,
        soft_penalty=lambda s, c, cat: (
            max(0, c.n - _pool_count(s, c)) + POOL_OVER_PENALTY * max(0, _pool_count(s, c) - c.n)
        ),
    ),
    "no_class_before": Rule(
        hard_ok=lambda s, c, cat: all(x.start >= c.time for x in _sessions(s, c.days)),
        soft_penalty=lambda s, c, cat: sum(_hours_up(_minutes_before(x, c.time)) for x in _sessions(s, c.days)),
    ),
    "no_class_after": Rule(
        hard_ok=lambda s, c, cat: all(x.end <= c.time for x in _sessions(s, c.days)),
        soft_penalty=lambda s, c, cat: sum(_hours_up(_minutes_after(x, c.time)) for x in _sessions(s, c.days)),
    ),
    "day_off": Rule(
        hard_ok=lambda s, c, cat: not _sessions(s, [c.day]),
        soft_penalty=lambda s, c, cat: 1 if _sessions(s, [c.day]) else 0,
    ),
    "max_days": Rule(
        hard_ok=lambda s, c, cat: len(_by_day(s)) <= c.n,
        soft_penalty=lambda s, c, cat: max(0, len(_by_day(s)) - c.n),
    ),
    "max_hours_per_day": Rule(
        hard_ok=lambda s, c, cat: all(sum(x.end - x.start for x in ss) <= c.h * 60 for ss in _by_day(s).values()),
        soft_penalty=lambda s, c, cat: sum(
            max(0, sum(x.end - x.start for x in ss) // 60 - c.h) for ss in _by_day(s).values()
        ),
    ),
    "free_block": Rule(
        hard_ok=lambda s, c, cat: all(_block_overlap(x, c) == 0 for x in _sessions(s, _block_days(c))),
        soft_penalty=lambda s, c, cat: sum(_hours_up(_block_overlap(x, c)) for x in _sessions(s, _block_days(c))),
    ),
    "max_gap": Rule(
        hard_ok=lambda s, c, cat: all(gap * 60 <= c.minutes for gap in _gaps_in_hours(s)),
        soft_penalty=lambda s, c, cat: sum(max(0, gap - c.minutes // 60) for gap in _gaps_in_hours(s)),
    ),
    "compact": Rule(
        hard_ok=_compact_hard,
        soft_penalty=lambda s, c, cat: sum(_gaps_in_hours(s)),
    ),
    "prefer_group": Rule(hard_ok=_member_hard_ok, soft_penalty=_member_soft),
    "avoid_group": Rule(hard_ok=_member_hard_ok, soft_penalty=_member_soft),
    "prefer_instructor": Rule(hard_ok=_member_hard_ok, soft_penalty=_member_soft),
    "avoid_instructor": Rule(hard_ok=_member_hard_ok, soft_penalty=_member_soft),
    "min_credits": Rule(
        hard_ok=lambda s, c, cat: _credits(s, cat) >= round(c.n * CREDIT_SCALE),
        soft_penalty=lambda s, c, cat: _credits_up(max(0, round(c.n * CREDIT_SCALE) - _credits(s, cat))),
    ),
    "max_credits": Rule(
        hard_ok=lambda s, c, cat: _credits(s, cat) <= round(c.n * CREDIT_SCALE),
        soft_penalty=lambda s, c, cat: _credits_up(max(0, _credits(s, cat) - round(c.n * CREDIT_SCALE))),
    ),
}


def rule_for(kind: str) -> Rule:
    if kind not in RULES:
        raise NotImplementedError(f"no invariant rule for constraint kind {kind!r}; add it to RULES")
    return RULES[kind]


def selectable_courses(constraints) -> set[str]:
    """Courses the solver may schedule: those in a must_take or a take_n_of pool, of either strength."""
    found = set()
    for c in constraints:
        if c.kind == "must_take":
            found.add(c.course)
        elif c.kind == "take_n_of":
            found.update(c.courses)
    return found


def clashes(a, b) -> bool:
    return a.day == b.day and a.start < b.end and b.start < a.end


def schedule_key(schedule) -> frozenset:
    """Identity on the week grid: (course, component, merged option)."""
    return frozenset((ch.course, ch.component, frozenset(ch.option.group_ids)) for ch in schedule.choices)


# ---- the checks ----

def assert_valid_schedule(schedule, catalog, constraints):
    courses = {c.id: c for c in catalog.courses}
    assert schedule.choices, "a schedule must contain at least one course"

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
        assert ch.register_for, f"{ch.course} {ch.component}: no group to register for"
        assert set(ch.register_for) <= set(ch.option.group_ids), (
            f"{ch.course} {ch.component}: register_for {ch.register_for} isn't within {ch.option.group_ids}"
        )
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
            assert rule_for(c.kind).hard_ok(schedule, c, catalog), f"hard constraint broken: {c}"

    # breakdown: every constraint, hard and soft, so the user can see what a schedule does and doesn't satisfy
    assert [item.constraint for item in schedule.breakdown] == list(constraints), (
        "breakdown must list every constraint once, in order"
    )
    for item in schedule.breakdown:
        c = item.constraint
        expected = 0 if c.strength == "hard" else rule_for(c.kind).soft_penalty(schedule, c, catalog)
        assert item.penalty == expected, f"{c}: penalty {item.penalty}, expected {expected}"
        assert item.weighted == c.weight * expected, f"{c}: wrong weighted penalty"
        assert item.met == (expected == 0), f"{c}: met={item.met} with penalty {expected}"
    assert schedule.penalty == sum(item.weighted for item in schedule.breakdown), "total isn't the sum of the breakdown"

    _assert_ties_are_disclosed(schedule, catalog, constraints)


def _assert_ties_are_disclosed(schedule, catalog, constraints):
    """Every member of an option as good as the recommended one is recommended too, or offered as an alternative."""
    rules = [c for c in constraints if c.strength == "soft" and c.kind in MEMBER_KINDS]

    for ch in schedule.choices:
        def penalties(group):
            return [member_penalty(c, ch.course, ch.component, group, catalog) for c in rules]

        def cost(group):
            return sum(c.weight * p for c, p in zip(rules, penalties(group)))

        by_id = {g.group: g for g in ch.option.groups}
        recommended = by_id[ch.register_for[0]]
        assert cost(recommended) == min(cost(g) for g in ch.option.groups), f"{ch.course}: didn't recommend the best member"
        for gid in ch.register_for:
            assert penalties(by_id[gid]) == penalties(recommended), f"{gid} doesn't have the same breakdown as the rest"

        alternatives = {alt.group: alt for alt in ch.alternatives}
        for g in ch.option.groups:
            ties = cost(g) == cost(recommended) and penalties(g) != penalties(recommended)
            assert (g.group in alternatives) == ties, f"{ch.course} {g.group}: tied alternative listed wrongly"
            if ties:
                pairs = list(zip(rules, penalties(g), penalties(recommended)))
                assert alternatives[g.group].breaks == [c for c, alt, rec in pairs if alt > rec]
                assert alternatives[g.group].instead_of == [c for c, alt, rec in pairs if alt < rec]
        assert (ch.register_note is not None) == bool(ch.alternatives), f"{ch.course}: tied options need a note"


def assert_valid_results(result, catalog, constraints, k):
    schedules = result.schedules
    assert isinstance(schedules, list)
    assert len(schedules) <= k
    for s in schedules:
        assert_valid_schedule(s, catalog, constraints)

    keys = [schedule_key(s) for s in schedules]
    assert len(keys) == len(set(keys)), "two results look identical on the grid"

    penalties = [s.penalty for s in schedules]
    assert penalties == sorted(penalties), f"results aren't ranked best first: {penalties}"

    if schedules:
        assert result.conflict == [], "a solvable result can't report a conflict"

    if getattr(result, "timed_out", False):
        # fewer than k is "there may be more", not "only N possible"
        note = result.note or ""
        assert "stopped" in note.lower() or "longer than" in note, f"a timed-out result must say it stopped: {note!r}"
        assert result.conflict == [], "a timed-out search can't name a conflict it didn't finish finding"
    elif 0 < len(schedules) < k:
        assert result.note, f"only {len(schedules)} results for k={k} but no note"
        match = re.search(r"\bonly (\d+)\b", result.note, re.IGNORECASE)
        assert match and int(match.group(1)) == len(schedules), f"note doesn't say 'only {len(schedules)}': {result.note!r}"
    elif len(schedules) == k:
        assert result.note is None, f"k results found but there's a note: {result.note!r}"
