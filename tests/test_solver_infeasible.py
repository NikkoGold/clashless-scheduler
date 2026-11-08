"""When nothing fits, name a minimal set of constraints that conflict."""
from scheduler.models import (
    AvoidGroup,
    DayOff,
    ExcludeCourse,
    MaxCredits,
    MinCredits,
    MustTake,
    NoClassAfter,
    PreferInstructor,
    TakeNOf,
)
from scheduler.solver import solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import entry_for
from tests.helpers.oracle import checked_solve


def friday_only():
    return catalog(course("CS201", lecture=[grp("1", "Fri 10-12")]))


def test_day_off_and_a_friday_only_course_are_both_named():
    constraints = must("CS201") + [DayOff(day="Fri")]
    result = checked_solve(friday_only(), constraints)
    assert result.schedules == []
    assert result.conflict == constraints
    assert "CS201" in result.note and "Fri" in result.note


def test_a_constraint_unrelated_to_the_conflict_is_not_named():
    harmless = NoClassAfter(time="22:00")
    result = checked_solve(friday_only(), must("CS201") + [harmless, DayOff(day="Fri")])
    assert result.conflict == must("CS201") + [DayOff(day="Fri")]


def test_two_required_courses_that_always_clash_are_named_without_a_third():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 11-13")]),
        course("C", lecture=[grp("1", "Mon 10-12")]),
    )
    result = checked_solve(cat, must("A", "B", "C"))
    assert result.conflict == must("A", "B")


def test_a_course_whose_own_components_always_clash_is_named_alone():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")], lab=[grp("11", "Sun 11-13")]))
    result = checked_solve(cat, must("A"))
    assert result.conflict == must("A")


def test_must_take_and_exclude_of_the_same_course_conflict():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]))
    constraints = must("A") + [ExcludeCourse(course="A")]
    assert checked_solve(cat, constraints).conflict == constraints


def test_min_and_max_credits_that_cannot_both_hold_conflict():
    # A or B alone meets the max, A and B together meet the min, and nothing meets both
    cat = catalog(
        course("A", credits=5, lecture=[grp("1", "Sun 10-12")]),
        course("B", credits=5, lecture=[grp("1", "Mon 10-12")]),
    )
    limits = [MinCredits(n=10), MaxCredits(n=5)]
    constraints = [MustTake(course="A", strength="soft"), MustTake(course="B", strength="soft")] + limits
    assert checked_solve(cat, constraints).conflict == limits


def test_a_pool_too_small_after_exclusions_is_a_conflict_not_an_error():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 10-12")]),
    )
    constraints = [TakeNOf(courses=["A", "B"], n=2), ExcludeCourse(course="A")]
    assert checked_solve(cat, constraints).conflict == constraints


def test_half_credits_can_exceed_a_limit_between_whole_numbers():
    cat = catalog(
        course("A", credits=3.5, lecture=[grp("1", "Sun 10-12")]),
        course("B", credits=3.5, lecture=[grp("1", "Mon 10-12")]),
    )
    constraints = must("A", "B") + [MaxCredits(n=6.5)]
    assert checked_solve(cat, constraints).conflict == constraints


def test_a_hard_pool_that_contradicts_required_courses_gives_no_schedule():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 10-12")]),
    )
    constraints = must("A", "B") + [TakeNOf(courses=["A", "B"], n=1)]
    result = checked_solve(cat, constraints)
    assert result.schedules == []
    assert result.conflict == constraints


def test_a_soft_pool_never_causes_a_conflict():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 10-12")]),
    )
    constraints = must("A", "B") + [TakeNOf(courses=["A", "B"], n=1, strength="soft")]
    result = checked_solve(cat, constraints)
    assert len(result.schedules) == 1
    assert result.conflict == []


def test_hard_rules_leaving_a_component_no_groups_conflict_with_taking_the_course():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12", instructor="Cohen")]))
    constraints = must("A") + [
        PreferInstructor(name="Cohen"),
        AvoidGroup(course="A", component="lecture", group="1"),
    ]
    assert checked_solve(cat, constraints).conflict == [constraints[0], constraints[2]]


def separate_conflicts():
    return catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 10-12")]),
        course("C", lecture=[grp("1", "Mon 10-12")]),
        course("D", lecture=[grp("1", "Mon 10-12")]),
    )


def test_with_two_separate_conflicts_one_minimal_set_is_named():
    cat = separate_conflicts()
    conflict = checked_solve(cat, must("A", "B", "C", "D")).conflict
    assert conflict in (must("A", "B"), must("C", "D"))


def test_every_constraint_named_is_needed_for_the_conflict():
    cat = separate_conflicts()
    conflict = checked_solve(cat, must("A", "B", "C", "D")).conflict
    assert solve(cat, conflict).schedules == []
    for dropped in conflict:
        assert solve(cat, [c for c in conflict if c != dropped]).schedules


def test_the_same_conflict_is_named_every_time():
    cat = separate_conflicts()
    constraints = must("A", "B", "C", "D")
    assert solve(cat, constraints).conflict == solve(cat, constraints).conflict


def test_a_soft_rule_never_causes_a_conflict():
    constraints = must("CS201") + [DayOff(day="Fri", strength="soft")]
    result = checked_solve(friday_only(), constraints)
    assert len(result.schedules) == 1
    assert result.conflict == []
    assert entry_for(result.schedules[0], constraints[1]).met is False


def test_no_hard_rules_involved_still_explains_that_nothing_fits():
    # A's lecture and lab always clash, and A is only wanted softly: no rule can be blamed
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")], lab=[grp("11", "Sun 11-13")]))
    result = checked_solve(cat, [MustTake(course="A", strength="soft")])
    assert result.schedules == []
    assert result.conflict == []
    assert result.nothing_to_schedule
    assert result.note


# ---- dropping a course request must not hide it from the conflict ----
# Dropping a must_take used to remove the course from the schedulable set as well, which could make
# the request *harder* (only excluded courses left, and a schedule is never empty). The search then
# blamed the wrong rule. Now the full request's courses stay available while searching.

def sunday_b_monday_a():
    return catalog(course("A", lecture=[grp("1", "Mon 10-12")]), course("B", lecture=[grp("1", "Sun 10-12")]))


def test_a_required_course_is_named_rather_than_an_unrelated_exclusion():
    must_b, sun_off = MustTake(course="B"), DayOff(day="Sun")
    constraints = [must_b, sun_off, ExcludeCourse(course="A"), MustTake(course="A", strength="soft")]
    result = checked_solve(sunday_b_monday_a(), constraints)
    assert result.conflict == [must_b, sun_off]
    assert not result.nothing_to_schedule
    assert "B" in result.note and "Sun" in result.note and "exclude" not in result.note


def test_a_hard_pool_is_named_rather_than_an_unrelated_exclusion():
    pool, sun_off = TakeNOf(courses=["B"], n=1), DayOff(day="Sun")
    constraints = [pool, sun_off, ExcludeCourse(course="A"), MustTake(course="A", strength="soft")]
    assert checked_solve(sunday_b_monday_a(), constraints).conflict == [pool, sun_off]


def test_a_course_that_can_never_be_taken_is_named_alongside_another_required_course():
    # found by Hypothesis: A's lecture and lab meet at the same time, so A alone is impossible
    a = course("A", credits=2, lecture=[grp("1", "Sun 8-9", instructor="Cohen")],
               lab=[grp("1", "Sun 8-9", instructor="Cohen")])
    cat = catalog(a, course("B", credits=2, lecture=[grp("1", "Sun 8-9", instructor="Cohen")]),
                  course("C", credits=2, lecture=[grp("1", "Sun 8-9", instructor="Cohen")]))
    constraints = must("A", "B") + [MustTake(course="A", strength="soft", weight=1)]
    result = checked_solve(cat, constraints)
    assert result.conflict == must("A")
    assert not result.nothing_to_schedule


def test_with_separate_conflicts_the_one_naming_requested_courses_is_preferred():
    # A and B always clash, and the credit range is impossible on its own: both are conflicts.
    # Course requests are tried for dropping last, so the conflict names the courses the user asked for.
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]), course("B", lecture=[grp("1", "Sun 11-13")]))
    constraints = must("A", "B") + [MinCredits(n=6), MaxCredits(n=3)]
    assert checked_solve(cat, constraints).conflict == must("A", "B")


def test_excluding_every_wanted_course_says_nothing_is_left_to_schedule():
    excl_a, excl_b = ExcludeCourse(course="A"), ExcludeCourse(course="B")
    constraints = [MustTake(course="A", strength="soft"), MustTake(course="B", strength="soft"), excl_a, excl_b]
    result = checked_solve(sunday_b_monday_a(), constraints)
    assert result.conflict == [excl_a, excl_b]
    assert result.nothing_to_schedule
    assert "exclude A + exclude B" in result.note and "no course" in result.note


def test_a_single_rule_leaving_nothing_to_schedule_says_so():
    excl_a = ExcludeCourse(course="A")
    result = checked_solve(sunday_b_monday_a(), [MustTake(course="A", strength="soft"), excl_a])
    assert result.conflict == [excl_a]
    assert result.nothing_to_schedule
    assert "exclude A" in result.note and "no course" in result.note


def test_hard_rules_that_conflict_by_themselves_do_not_blame_the_empty_schedule_rule():
    result = checked_solve(friday_only(), must("CS201") + [DayOff(day="Fri")])
    assert not result.nothing_to_schedule


def test_a_solvable_request_reports_nothing_to_schedule_as_false():
    assert not checked_solve(friday_only(), must("CS201")).nothing_to_schedule
