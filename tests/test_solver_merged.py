"""Merged options: scored by their best member, and the result says which one to register for."""
from scheduler.models import AvoidGroup, AvoidInstructor, PreferGroup, PreferInstructor
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import choice_of, entry_for
from tests.helpers.oracle import checked_solve


def labs(*extra):
    # labs 11 and 12 meet at the same time, so they merge into one option
    return catalog(course("A",
        lecture=[grp("1", "Sun 10-12")],
        lab=[grp("11", "Mon 10-12", instructor="Cohen"), grp("12", "Mon 10-12", instructor="Levi"), *extra],
    ))


def lab_choice(result, rank=0):
    return choice_of(result.schedules[rank], "A", "lab")


def prefer_group(group, **kwargs):
    return PreferGroup(course="A", component="lab", group=group, **kwargs)


def avoid_group(group, **kwargs):
    return AvoidGroup(course="A", component="lab", group=group, **kwargs)


def test_soft_prefer_instructor_scores_the_option_by_their_member_and_names_it():
    rule = PreferInstructor(name="Cohen", strength="soft")
    result = checked_solve(labs(), must("A") + [rule])
    assert lab_choice(result).option.group_ids == ("11", "12")
    assert lab_choice(result).register_for == ["11"]
    assert result.schedules[0].penalty == 0


def test_soft_avoid_instructor_names_the_other_member():
    rule = AvoidInstructor(name="Cohen", strength="soft")
    result = checked_solve(labs(), must("A") + [rule])
    assert lab_choice(result).register_for == ["12"]
    assert result.schedules[0].penalty == 0


def test_no_relevant_preference_lists_every_member():
    result = checked_solve(labs(), must("A"))
    assert lab_choice(result).register_for == ["11", "12"]


def test_one_member_must_satisfy_the_whole_breakdown_when_preferences_pull_apart():
    # Cohen teaches 11 (weight 3) but group 12 is wanted (weight 8): take 12 and lose only 3
    instructor = PreferInstructor(name="Cohen", strength="soft", weight=3)
    group = prefer_group("12", strength="soft", weight=8)
    result = checked_solve(labs(), must("A") + [instructor, group])
    schedule = result.schedules[0]
    assert lab_choice(result).register_for == ["12"]
    assert schedule.penalty == 3
    assert entry_for(schedule, instructor).met is False
    assert entry_for(schedule, group).met is True
    assert lab_choice(result).alternatives == []  # 11 costs 8, so it isn't a tie
    assert lab_choice(result).register_note is None


def tied_rules():
    # 11 breaks the group rule and 12 breaks the instructor rule, for the same cost
    return (
        PreferInstructor(name="Cohen", strength="soft", weight=5),
        prefer_group("12", strength="soft", weight=5),
    )


def test_members_tied_on_cost_but_not_on_breakdown_are_offered_as_alternatives():
    instructor, group = tied_rules()
    result = checked_solve(labs(), must("A") + [instructor, group])
    choice = lab_choice(result)
    assert choice.register_for == ["11"]
    assert [(a.group, a.breaks, a.instead_of) for a in choice.alternatives] == [("12", [instructor], [group])]
    assert result.schedules[0].penalty == 5


def test_tied_alternative_is_described_in_plain_language():
    result = checked_solve(labs(), must("A") + list(tied_rules()))
    assert lab_choice(result).register_note == (
        "Register for 11, or 12, which breaks prefer Cohen instead of prefer A lab group 12"
    )


def test_tied_alternative_shows_even_when_only_one_result_was_asked_for():
    result = checked_solve(labs(), must("A") + list(tied_rules()), k=1)
    assert [a.group for a in lab_choice(result).alternatives] == ["12"]


def test_tied_alternative_does_not_use_up_a_top_k_place():
    # one lab option at Mon and another on Tue: two results, not three
    result = checked_solve(labs(grp("13", "Tue 10-12")), must("A") + list(tied_rules()))
    assert len(result.schedules) == 2


def test_members_with_the_same_breakdown_are_not_alternatives_of_each_other():
    result = checked_solve(labs(), must("A"))
    assert lab_choice(result).alternatives == []
    assert lab_choice(result).register_note is None


def test_soft_preferences_do_not_split_a_merged_option_into_two_results():
    result = checked_solve(labs(), must("A") + [PreferInstructor(name="Cohen", strength="soft")])
    assert len(result.schedules) == 1
    assert "only 1" in result.note.lower()


def test_hard_avoid_group_removes_only_that_member():
    result = checked_solve(labs(), must("A") + [avoid_group("11")])
    assert lab_choice(result).option.group_ids == ("12",)
    assert lab_choice(result).register_for == ["12"]


def test_hard_prefer_group_keeps_only_that_member():
    result = checked_solve(labs(), must("A") + [prefer_group("12")])
    assert lab_choice(result).option.group_ids == ("12",)


def test_hard_prefer_instructor_keeps_only_their_member():
    result = checked_solve(labs(), must("A") + [PreferInstructor(name="Cohen")])
    assert lab_choice(result).option.group_ids == ("11",)


def test_hard_avoid_instructor_removes_only_their_member():
    result = checked_solve(labs(), must("A") + [AvoidInstructor(name="Cohen")])
    assert lab_choice(result).option.group_ids == ("12",)


def test_hard_avoiding_every_member_removes_the_option():
    result = checked_solve(labs(grp("13", "Tue 10-12")), must("A") + [avoid_group("11"), avoid_group("12")])
    assert [choice_of(s, "A", "lab").option.group_ids for s in result.schedules] == [("13",)]


def test_soft_preference_for_a_member_a_hard_rule_removed_is_marked_broken():
    rule = PreferInstructor(name="Cohen", strength="soft")
    result = checked_solve(labs(), must("A") + [avoid_group("11"), rule])
    assert lab_choice(result).register_for == ["12"]
    assert result.schedules[0].penalty == 5
