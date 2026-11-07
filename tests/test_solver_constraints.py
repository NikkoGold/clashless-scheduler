"""Course selection, hard rules and input errors."""
import pytest

from scheduler.models import (
    AvoidGroup,
    AvoidInstructor,
    DayOff,
    ExcludeCourse,
    FreeBlock,
    MaxCredits,
    MaxDays,
    MaxGap,
    MaxHoursPerDay,
    MinCredits,
    MustTake,
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


def one_group_courses(*ids, credits=3):
    """Non-clashing single-group courses, one per day."""
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    return catalog(*[course(cid, credits=credits, lecture=[grp("1", f"{days[i]} 10-12")]) for i, cid in enumerate(ids)])


def selections(result) -> list[frozenset]:
    return [frozenset(courses_of(s)) for s in result.schedules]


def lecture_choices(result, course_id="A") -> list[tuple[str, ...]]:
    return [groups_of(s, course_id, "lecture") for s in result.schedules]


# ---- take_n_of and course selection ----

def test_take_n_of_picks_n_courses_from_the_pool():
    result = checked_solve(one_group_courses("A", "B", "C"), [TakeNOf(courses=["A", "B", "C"], n=2)])
    assert set(selections(result)) == {frozenset("AB"), frozenset("AC"), frozenset("BC")}
    assert "only 3" in result.note.lower()


def test_take_n_of_with_pool_equal_to_n_takes_every_course():
    result = checked_solve(one_group_courses("A", "B"), [TakeNOf(courses=["A", "B"], n=2)])
    assert selections(result) == [frozenset("AB")]


def test_take_n_of_with_pool_smaller_than_n_is_an_error_before_solving():
    with pytest.raises(ValueError, match="take_n_of"):
        solve(one_group_courses("A"), [TakeNOf(courses=["A"], n=2)])


def test_required_course_in_the_pool_uses_up_one_of_its_picks():
    constraints = must("A") + [TakeNOf(courses=["A", "B", "C"], n=2)]
    result = checked_solve(one_group_courses("A", "B", "C"), constraints)
    assert set(selections(result)) == {frozenset("AB"), frozenset("AC")}


def test_required_course_outside_the_pool_does_not_count_toward_n():
    constraints = must("D") + [TakeNOf(courses=["A", "B", "C"], n=2)]
    result = checked_solve(one_group_courses("A", "B", "C", "D"), constraints)
    assert set(selections(result)) == {frozenset("ABD"), frozenset("ACD"), frozenset("BCD")}


def test_soft_course_outside_the_pool_is_scored_separately_from_the_pool():
    constraints = must("D") + [MustTake(course="E", strength="soft"), TakeNOf(courses=["A", "B"], n=1)]
    result = checked_solve(one_group_courses("A", "B", "D", "E"), constraints)
    penalties = {frozenset(courses_of(s)): s.penalty for s in result.schedules}
    assert penalties == {frozenset("ADE"): 0, frozenset("BDE"): 0, frozenset("AD"): 5, frozenset("BD"): 5}


def test_soft_pool_only_counts_courses_in_that_pool():
    # D is required outside the pool and never uses up a pick. Weight 5: each one short costs 5, one over costs 10.
    constraints = must("D") + [TakeNOf(courses=["A", "B", "C"], n=2, strength="soft")]
    result = checked_solve(one_group_courses("A", "B", "C", "D"), constraints, k=10)
    penalties = {frozenset(courses_of(s)): s.penalty for s in result.schedules}
    assert penalties == {
        frozenset("ABD"): 0, frozenset("ACD"): 0, frozenset("BCD"): 0,
        frozenset("AD"): 5, frozenset("BD"): 5, frozenset("CD"): 5,
        frozenset("D"): 10,
        frozenset("ABCD"): 10,
    }


def test_soft_pool_shows_one_course_short_before_one_course_over():
    constraints = [TakeNOf(courses=["A", "B", "C"], n=2, strength="soft")]
    result = checked_solve(one_group_courses("A", "B", "C"), constraints, k=7)
    sizes = [len(courses_of(s)) for s in result.schedules]
    assert sizes == [2, 2, 2, 1, 1, 1, 3]
    assert [s.penalty for s in result.schedules] == [0, 0, 0, 5, 5, 5, 10]


def test_soft_pool_can_be_exceeded_to_fit_required_courses():
    constraints = must("A", "B") + [TakeNOf(courses=["A", "B"], n=1, strength="soft")]
    result = checked_solve(one_group_courses("A", "B"), constraints)
    assert selections(result) == [frozenset("AB")]
    assert result.schedules[0].penalty == 10
    assert entry_for(result.schedules[0], constraints[2]).met is False


def test_two_pools_are_counted_separately():
    constraints = [TakeNOf(courses=["A", "B"], n=1), TakeNOf(courses=["C", "D"], n=1)]
    result = checked_solve(one_group_courses("A", "B", "C", "D"), constraints)
    assert set(selections(result)) == {frozenset("AC"), frozenset("AD"), frozenset("BC"), frozenset("BD")}


def test_overlapping_pools_can_share_a_course():
    constraints = [TakeNOf(courses=["A", "B"], n=1), TakeNOf(courses=["B", "C"], n=1)]
    result = checked_solve(one_group_courses("A", "B", "C"), constraints)
    assert set(selections(result)) == {frozenset("B"), frozenset("AC")}


def test_excluded_course_is_not_picked_from_a_pool():
    constraints = [ExcludeCourse(course="A"), TakeNOf(courses=["A", "B"], n=1)]
    result = checked_solve(one_group_courses("A", "B"), constraints)
    assert selections(result) == [frozenset("B")]


def test_course_outside_every_pool_and_must_take_is_not_scheduled():
    result = checked_solve(one_group_courses("A", "B", "D"), [TakeNOf(courses=["A", "B"], n=2)])
    assert selections(result) == [frozenset("AB")]


def test_soft_must_take_is_dropped_when_it_clashes_with_a_required_course():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 10-12")]),
    )
    soft_a = MustTake(course="A", strength="soft")
    result = checked_solve(cat, must("B") + [soft_a])
    assert selections(result) == [frozenset("B")]
    assert entry_for(result.schedules[0], soft_a).met is False


def test_soft_must_take_that_fits_ranks_above_leaving_it_out():
    soft_a = MustTake(course="A", strength="soft")
    result = checked_solve(one_group_courses("A", "B"), must("B") + [soft_a])
    assert selections(result) == [frozenset("AB"), frozenset("B")]


def test_ruling_out_a_schedule_keeps_larger_schedules_containing_it():
    # Skipping A costs 1, but taking A costs 2 hours x 5 under the early-class rule,
    # so B alone is found first. Ruling it out must not also rule out A + B.
    cat = catalog(
        course("A", lecture=[grp("1", "Mon 8-10")]),
        course("B", lecture=[grp("1", "Sun 10-12")]),
    )
    constraints = must("B") + [
        MustTake(course="A", strength="soft", weight=1),
        NoClassBefore(time="10:00", strength="soft", weight=5),
    ]
    result = checked_solve(cat, constraints)
    assert selections(result) == [frozenset("B"), frozenset("AB")]
    assert [s.penalty for s in result.schedules] == [1, 10]


def test_an_empty_schedule_is_never_returned():
    result = checked_solve(one_group_courses("A"), [MustTake(course="A", strength="soft")])
    assert selections(result) == [frozenset("A")]
    assert "only 1" in result.note.lower()


# ---- hard time and day rules ----

def test_no_class_before_allows_a_class_starting_exactly_then():
    cat = catalog(course("A", lecture=[grp("1", "Sun 8-10"), grp("2", "Sun 10-12")]))
    assert lecture_choices(checked_solve(cat, must("A") + [NoClassBefore(time="10:00")])) == [("2",)]


def test_no_class_before_rules_out_a_session_straddling_the_time():
    cat = catalog(course("A", lecture=[grp("1", "Sun 9-11"), grp("2", "Sun 10-12")]))
    assert lecture_choices(checked_solve(cat, must("A") + [NoClassBefore(time="10:00")])) == [("2",)]


def test_no_class_before_applies_only_to_the_named_days():
    cat = catalog(course("A", lecture=[grp("1", "Mon 8-10"), grp("2", "Sun 8-10")]))
    constraints = must("A") + [NoClassBefore(time="10:00", days=["Mon"])]
    assert lecture_choices(checked_solve(cat, constraints)) == [("2",)]


def test_no_class_before_a_time_off_the_hour_rules_out_a_class_starting_earlier():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12"), grp("2", "Sun 11-13")]))
    assert lecture_choices(checked_solve(cat, must("A") + [NoClassBefore(time="10:30")])) == [("2",)]


def test_no_class_after_allows_a_class_ending_exactly_then():
    cat = catalog(course("A", lecture=[grp("1", "Sun 14-16"), grp("2", "Sun 15-17")]))
    assert lecture_choices(checked_solve(cat, must("A") + [NoClassAfter(time="16:00")])) == [("1",)]


def test_no_class_after_applies_only_to_the_named_days():
    cat = catalog(course("A", lecture=[grp("1", "Mon 15-17"), grp("2", "Sun 15-17")]))
    constraints = must("A") + [NoClassAfter(time="16:00", days=["Mon"])]
    assert lecture_choices(checked_solve(cat, constraints)) == [("2",)]


def test_day_off_rules_out_groups_meeting_that_day():
    cat = catalog(course("A", lecture=[grp("1", "Fri 10-12"), grp("2", "Sun 10-12")]))
    assert lecture_choices(checked_solve(cat, must("A") + [DayOff(day="Fri")])) == [("2",)]


def test_day_off_outside_the_week_has_no_effect():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]))
    assert lecture_choices(checked_solve(cat, must("A") + [DayOff(day="Sat")])) == [("1",)]


def test_max_days_limits_how_many_days_have_classes():
    # A is on Sun; B on Sun or Mon; C on Mon or Tue. Only B Mon + C Tue uses 3 days.
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 12-14"), grp("2", "Mon 10-12")]),
        course("C", lecture=[grp("1", "Mon 12-14"), grp("2", "Tue 10-12")]),
    )
    result = checked_solve(cat, must("A", "B", "C") + [MaxDays(n=2)])
    pairs = {(groups_of(s, "B", "lecture"), groups_of(s, "C", "lecture")) for s in result.schedules}
    assert pairs == {(("1",), ("1",)), (("1",), ("2",)), (("2",), ("1",))}


def hours_catalog():
    return catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 12-14"), grp("2", "Mon 10-12")]),
    )


def test_max_hours_per_day_rules_out_too_long_a_day():
    result = checked_solve(hours_catalog(), must("A", "B") + [MaxHoursPerDay(h=3)])
    assert lecture_choices(result, "B") == [("2",)]


def test_max_hours_per_day_allows_a_day_of_exactly_that_length():
    result = checked_solve(hours_catalog(), must("A", "B") + [MaxHoursPerDay(h=4)])
    assert set(lecture_choices(result, "B")) == {("1",), ("2",)}


def test_free_block_allows_a_class_ending_as_it_starts():
    cat = catalog(course("A", lecture=[grp("1", "Sun 11-13"), grp("2", "Sun 10-12")]))
    constraints = must("A") + [FreeBlock(day="Sun", start="12:00", end="13:00")]
    assert lecture_choices(checked_solve(cat, constraints)) == [("2",)]


def test_free_block_with_a_day_leaves_other_days_alone():
    cat = catalog(course("A", lecture=[grp("1", "Sun 12-14"), grp("2", "Mon 12-14")]))
    constraints = must("A") + [FreeBlock(day="Sun", start="12:00", end="13:00")]
    assert lecture_choices(checked_solve(cat, constraints)) == [("2",)]


def test_free_block_without_a_day_applies_every_day():
    cat = catalog(course("A", lecture=[grp("1", "Sun 12-14"), grp("2", "Mon 12-14"), grp("3", "Tue 10-12")]))
    constraints = must("A") + [FreeBlock(start="12:00", end="13:00")]
    assert lecture_choices(checked_solve(cat, constraints)) == [("3",)]


def test_max_gap_rules_out_a_longer_gap():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 8-10")]),
        course("B", lecture=[grp("1", "Sun 11-13"), grp("2", "Sun 12-14")]),
    )
    result = checked_solve(cat, must("A", "B") + [MaxGap(minutes=60)])
    assert lecture_choices(result, "B") == [("1",)]


def test_max_gap_does_not_apply_across_days():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 8-10")]),
        course("B", lecture=[grp("1", "Mon 18-20")]),
    )
    assert len(checked_solve(cat, must("A", "B") + [MaxGap(minutes=0)]).schedules) == 1


def test_max_gap_zero_allows_back_to_back_classes():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 8-10")]),
        course("B", lecture=[grp("1", "Sun 10-12"), grp("2", "Sun 11-13")]),
    )
    result = checked_solve(cat, must("A", "B") + [MaxGap(minutes=0)])
    assert lecture_choices(result, "B") == [("1",)]


# ---- hard group and instructor rules ----

def two_groups():
    return catalog(course("A", lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")]))


def test_hard_prefer_group_forces_that_group():
    constraints = must("A") + [PreferGroup(course="A", component="lecture", group="2")]
    assert lecture_choices(checked_solve(two_groups(), constraints)) == [("2",)]


def test_hard_avoid_group_removes_that_group():
    constraints = must("A") + [AvoidGroup(course="A", component="lecture", group="1")]
    assert lecture_choices(checked_solve(two_groups(), constraints)) == [("2",)]


def test_hard_prefer_group_has_no_effect_when_the_course_is_not_taken():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 10-12")]),
    )
    constraints = must("B") + [PreferGroup(course="A", component="lecture", group="1")]
    assert selections(checked_solve(cat, constraints)) == [frozenset("B")]


def instructor_catalog():
    return catalog(
        course("A", lecture=[grp("1", "Sun 10-12", instructor="Cohen"), grp("2", "Mon 10-12", instructor="Levi")]),
        course("B", lecture=[grp("1", "Tue 10-12", instructor="Levi")]),
    )


def test_hard_prefer_instructor_applies_only_to_components_they_teach():
    # Cohen teaches a group of A's lecture but none of B's, so B is still schedulable
    result = checked_solve(instructor_catalog(), must("A", "B") + [PreferInstructor(name="Cohen")])
    assert lecture_choices(result) == [("1",)]
    assert selections(result) == [frozenset("AB")]


def test_hard_avoid_instructor_removes_their_groups():
    result = checked_solve(instructor_catalog(), must("A") + [AvoidInstructor(name="Cohen")])
    assert lecture_choices(result) == [("2",)]


# ---- hard credit rules ----

def credit_pool():
    return catalog(
        course("A", credits=3, lecture=[grp("1", "Sun 10-12")]),
        course("B", credits=5, lecture=[grp("1", "Mon 10-12")]),
    )


def test_min_credits_rules_out_too_few_credits():
    constraints = [TakeNOf(courses=["A", "B"], n=1), MinCredits(n=4)]
    assert selections(checked_solve(credit_pool(), constraints)) == [frozenset("B")]


def test_max_credits_rules_out_too_many_credits():
    constraints = [TakeNOf(courses=["A", "B"], n=1), MaxCredits(n=4)]
    assert selections(checked_solve(credit_pool(), constraints)) == [frozenset("A")]


def test_half_credits_add_up_exactly():
    cat = one_group_courses("A", "B", credits=3.5)
    assert selections(checked_solve(cat, must("A", "B") + [MinCredits(n=7)])) == [frozenset("AB")]


# ---- references that don't exist ----

@pytest.mark.parametrize(
    "constraint, name",
    [
        (MustTake(course="XX9"), "XX9"),
        (ExcludeCourse(course="XX9"), "XX9"),
        (TakeNOf(courses=["A", "XX9"], n=1), "XX9"),
        (PreferGroup(course="XX9", component="lecture", group="1"), "XX9"),
        (AvoidGroup(course="XX9", component="lecture", group="1"), "XX9"),
        (PreferGroup(course="A", component="seminar", group="1"), "seminar"),
        (AvoidGroup(course="A", component="seminar", group="1"), "seminar"),
        (PreferGroup(course="A", component="lecture", group="99"), "99"),
        (AvoidGroup(course="A", component="lecture", group="99"), "99"),
        (PreferInstructor(name="Nobody"), "Nobody"),
        (AvoidInstructor(name="Nobody"), "Nobody"),
    ],
    ids=lambda v: getattr(v, "kind", None),
)
def test_a_reference_to_something_not_in_the_catalog_is_an_error(constraint, name):
    with pytest.raises(ValueError, match=name):
        solve(two_groups(), must("A") + [constraint])
