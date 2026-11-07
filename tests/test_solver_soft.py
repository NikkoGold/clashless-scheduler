"""Soft rules, the score breakdown and ranking."""
import pytest

from scheduler.models import (
    AvoidGroup,
    AvoidInstructor,
    Compact,
    DayOff,
    ExcludeCourse,
    FreeBlock,
    MaxCredits,
    MaxDays,
    MaxGap,
    MaxHoursPerDay,
    MinCredits,
    NoClassAfter,
    NoClassBefore,
    PreferGroup,
    PreferInstructor,
    TakeNOf,
)
from scheduler.solver import solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import courses_of, entry_for, groups_of
from tests.helpers.oracle import checked_solve

SOFT = {"strength": "soft", "weight": 5}


def groups(*slots, course_id="A"):
    """A course whose lecture groups 1, 2, ... meet at these times."""
    return course(course_id, lecture=[grp(str(i + 1), s) for i, s in enumerate(slots)])


FIXED_A_8_10 = course("A", lecture=[grp("1", "Sun 8-10")])
FIXED_A_10_12 = course("A", lecture=[grp("1", "Sun 10-12")])
A_3CR = course("A", credits=3, lecture=[grp("1", "Sun 10-12")])
B_5CR = course("B", credits=5, lecture=[grp("1", "Mon 10-12")])
CREDIT_POOL = [TakeNOf(courses=["A", "B"], n=1)]
TEACHERS = course("A", lecture=[grp("1", "Sun 10-12", instructor="Cohen"), grp("2", "Mon 10-12", instructor="Levi")])

# id: (courses, other constraints, the soft rule, penalties in rank order)
CASES = {
    "exclude_course": (
        [FIXED_A_10_12, groups("Mon 10-12", course_id="B")], CREDIT_POOL,
        ExcludeCourse(course="A", **SOFT), [0, 5],
    ),
    "take_n_of": (
        [FIXED_A_10_12, groups("Mon 10-12", course_id="B")], [],
        TakeNOf(courses=["A", "B"], n=2, **SOFT), [0, 5, 5],
    ),
    "take_n_of over costs double": (
        [FIXED_A_10_12, groups("Mon 10-12", course_id="B")], [],
        TakeNOf(courses=["A", "B"], n=1, **SOFT), [0, 0, 10],
    ),
    "no_class_before scales with hours": (
        [groups("Sun 10-12", "Sun 9-11", "Sun 8-10")], must("A"),
        NoClassBefore(time="10:00", **SOFT), [0, 5, 10],
    ),
    "no_class_before off the hour rounds up": (
        [groups("Sun 11-13", "Sun 10-12")], must("A"),
        NoClassBefore(time="10:30", **SOFT), [0, 5],
    ),
    "no_class_after": (
        [groups("Sun 14-16", "Sun 15-17")], must("A"),
        NoClassAfter(time="16:00", **SOFT), [0, 5],
    ),
    "day_off": (
        [groups("Sun 10-12", "Fri 10-12")], must("A"),
        DayOff(day="Fri", **SOFT), [0, 5],
    ),
    "max_days": (
        [FIXED_A_10_12, groups("Sun 12-14", "Mon 10-12", course_id="B")], must("A", "B"),
        MaxDays(n=1, **SOFT), [0, 5],
    ),
    "max_hours_per_day": (
        [FIXED_A_10_12, groups("Sun 12-14", "Mon 10-12", course_id="B")], must("A", "B"),
        MaxHoursPerDay(h=2, **SOFT), [0, 10],
    ),
    "free_block": (
        [groups("Sun 10-12", "Sun 11-13")], must("A"),
        FreeBlock(day="Sun", start="12:00", end="13:00", **SOFT), [0, 5],
    ),
    "max_gap": (
        [FIXED_A_8_10, groups("Sun 11-13", "Sun 13-15", course_id="B")], must("A", "B"),
        MaxGap(minutes=60, **SOFT), [0, 10],
    ),
    "compact": (
        [FIXED_A_8_10, groups("Sun 10-12", "Sun 12-14", course_id="B")], must("A", "B"),
        Compact(weight=5), [0, 10],
    ),
    "compact ignores separate days": (
        [FIXED_A_8_10, groups("Mon 18-20", course_id="B")], must("A", "B"),
        Compact(weight=5), [0],
    ),
    "prefer_group": (
        [groups("Sun 10-12", "Mon 10-12")], must("A"),
        PreferGroup(course="A", component="lecture", group="2", **SOFT), [0, 5],
    ),
    "avoid_group": (
        [groups("Sun 10-12", "Mon 10-12")], must("A"),
        AvoidGroup(course="A", component="lecture", group="1", **SOFT), [0, 5],
    ),
    "prefer_instructor": (
        [TEACHERS], must("A"),
        PreferInstructor(name="Cohen", **SOFT), [0, 5],
    ),
    "avoid_instructor": (
        [TEACHERS], must("A"),
        AvoidInstructor(name="Cohen", **SOFT), [0, 5],
    ),
    "min_credits": (
        [A_3CR, B_5CR], CREDIT_POOL,
        MinCredits(n=5, **SOFT), [0, 10],
    ),
    "max_credits": (
        [A_3CR, B_5CR], CREDIT_POOL,
        MaxCredits(n=3, **SOFT), [0, 10],
    ),
    "half a credit short rounds up": (
        [course("A", credits=3.5, lecture=[grp("1", "Sun 10-12")])], must("A"),
        MinCredits(n=4, **SOFT), [5],
    ),
}


def with_groups_reversed(c: dict) -> dict:
    return {**c, "components": {name: list(reversed(gs)) for name, gs in c["components"].items()}}


# Both listing orders: the solver can land on the right order by chance, so one order proves little.
@pytest.mark.parametrize("listing", ["as written", "reversed"])
@pytest.mark.parametrize("courses, others, rule, penalties", CASES.values(), ids=CASES.keys())
def test_soft_rule_sets_the_ranking_and_the_breakdown(courses, others, rule, penalties, listing):
    if listing == "reversed":
        courses = [with_groups_reversed(c) for c in courses][::-1]
    constraints = others + [rule]
    result = checked_solve(catalog(*courses), constraints, k=len(penalties))
    assert [s.penalty for s in result.schedules] == penalties
    for schedule, expected in zip(result.schedules, penalties):
        item = entry_for(schedule, rule)
        assert item.weighted == expected
        assert item.met == (expected == 0)


def test_soft_rule_picks_the_group_that_satisfies_it():
    cat = catalog(groups("Fri 10-12", "Sun 10-12"))
    result = checked_solve(cat, must("A") + [DayOff(day="Fri", **SOFT)])
    assert groups_of(result.schedules[0], "A", "lecture") == ("2",)


# ---- weights and ranking ----

def weighed_catalog():
    # group 1 is Cohen's but 2 hours too early; group 2 is on time but Levi's
    return catalog(course("A", lecture=[
        grp("1", "Sun 8-10", instructor="Cohen"),
        grp("2", "Sun 10-12", instructor="Levi"),
    ]))


def test_heavier_weight_on_the_time_rule_picks_the_later_group():
    constraints = must("A") + [
        NoClassBefore(time="10:00", strength="soft", weight=8),
        PreferInstructor(name="Cohen", strength="soft", weight=3),
    ]
    result = checked_solve(weighed_catalog(), constraints)
    assert [groups_of(s, "A", "lecture") for s in result.schedules] == [("2",), ("1",)]
    assert [s.penalty for s in result.schedules] == [3, 16]


def test_heavier_weight_on_the_instructor_rule_picks_their_group():
    constraints = must("A") + [
        NoClassBefore(time="10:00", strength="soft", weight=3),
        PreferInstructor(name="Cohen", strength="soft", weight=8),
    ]
    result = checked_solve(weighed_catalog(), constraints)
    assert [groups_of(s, "A", "lecture") for s in result.schedules] == [("1",), ("2",)]
    assert [s.penalty for s in result.schedules] == [6, 8]


def test_k_smaller_than_the_number_of_schedules_returns_the_cheapest():
    cat = catalog(groups("Sun 8-10", "Sun 9-11", "Sun 10-12"))
    result = checked_solve(cat, must("A") + [NoClassBefore(time="10:00", **SOFT)], k=2)
    assert [s.penalty for s in result.schedules] == [0, 5]


def test_lower_ranked_results_still_satisfy_every_hard_rule():
    cat = catalog(groups("Fri 8-10", "Sun 8-10", "Mon 10-12"))
    constraints = must("A") + [DayOff(day="Fri"), NoClassBefore(time="10:00", **SOFT)]
    result = checked_solve(cat, constraints)
    assert [groups_of(s, "A", "lecture") for s in result.schedules] == [("3",), ("2",)]


def test_breakdown_shows_every_rule_hard_and_soft_met_and_broken():
    cat = catalog(groups("Sun 8-10"))
    required = must("A")[0]
    no_saturday = DayOff(day="Sat")
    day_off = DayOff(day="Fri", **SOFT)
    before = NoClassBefore(time="10:00", strength="soft", weight=2)
    schedule = checked_solve(cat, [required, no_saturday, day_off, before]).schedules[0]
    assert [(i.constraint, i.met, i.penalty, i.weighted) for i in schedule.breakdown] == [
        (required, True, 0, 0),
        (no_saturday, True, 0, 0),
        (day_off, True, 0, 0),
        (before, False, 2, 4),
    ]
    assert schedule.penalty == 4


def test_hard_rules_only_gives_zero_penalty_with_every_rule_shown_as_met():
    constraints = must("A")
    schedule = checked_solve(catalog(groups("Sun 10-12")), constraints).schedules[0]
    assert schedule.penalty == 0
    assert [(i.constraint, i.met) for i in schedule.breakdown] == [(constraints[0], True)]


def test_solving_twice_gives_the_same_order():
    cat = catalog(
        groups("Sun 8-10", "Sun 10-12", "Mon 8-10"),
        groups("Tue 8-10", "Tue 10-12", course_id="B"),
    )
    constraints = must("A", "B") + [NoClassBefore(time="10:00", **SOFT), DayOff(day="Mon", **SOFT)]
    first = solve(cat, constraints, k=5)
    second = solve(cat, constraints, k=5)
    assert first == second


def test_soft_course_choice_follows_the_preferences_across_a_pool():
    # both pool courses fit, but B is early; prefer A even though neither is required
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 8-10")]),
    )
    constraints = [TakeNOf(courses=["A", "B"], n=1), NoClassBefore(time="10:00", **SOFT)]
    result = checked_solve(cat, constraints)
    assert [courses_of(s) for s in result.schedules] == [{"A"}, {"B"}]
