import pytest

from scheduler.solver import solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import assert_valid_results
from tests.helpers.oracle import assert_matches_oracle


def run(cat, constraints, k=5):
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    assert_matches_oracle(result, cat, constraints, k)
    return result


def chosen(schedule, course_id, component):
    return next(ch.option.group_ids for ch in schedule.choices if (ch.course, ch.component) == (course_id, component))


def test_back_to_back_classes_do_not_clash():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 12-14")]),
    )
    assert len(run(cat, must("A", "B")).schedules) == 1


def test_overlapping_classes_clash():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 11-13")]),
    )
    assert run(cat, must("A", "B")).schedules == []


def test_multi_session_group_is_ruled_out_by_a_clash_on_its_second_session():
    # A lecture 1 is fine on Mon but clashes with B on Wed, so only A lecture 2 works
    cat = catalog(
        course("A", lecture=[grp("1", "Mon 10-12", "Wed 10-12"), grp("2", "Tue 10-12", "Thu 10-12")]),
        course("B", lecture=[grp("1", "Wed 11-13")]),
    )
    schedules = run(cat, must("A", "B")).schedules
    assert [chosen(s, "A", "lecture") for s in schedules] == [("2",)]


def test_multi_session_group_as_the_only_option_makes_it_infeasible():
    cat = catalog(
        course("A", lecture=[grp("1", "Mon 10-12", "Wed 10-12")]),
        course("B", lecture=[grp("1", "Wed 11-13")]),
    )
    assert run(cat, must("A", "B")).schedules == []


def test_any_lab_can_go_with_any_lecture():
    # parent is stored but not enforced: all 2 x 2 pairs are valid, including lecture 1 + lab 21
    cat = catalog(course("A",
        lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")],
        lab=[grp("11", "Tue 10-12", parent="1"), grp("21", "Wed 10-12", parent="2")],
    ))
    schedules = run(cat, must("A")).schedules
    pairs = {(chosen(s, "A", "lecture"), chosen(s, "A", "lab")) for s in schedules}
    assert len(pairs) == 4
    assert (("1",), ("21",)) in pairs


def test_attendance_required_false_does_not_change_the_results():
    def build(attendance_required):
        return catalog(
            course("A", lecture=[grp("1", "Sun 10-12", attendance_required=attendance_required), grp("2", "Mon 10-12")]),
            course("B", lecture=[grp("1", "Sun 10-12"), grp("2", "Tue 10-12")]),
        )
    keys = []
    for flag in (True, False):
        cat = build(flag)
        keys.append({frozenset((ch.course, ch.option.group_ids) for ch in s.choices)
                     for s in run(cat, must("A", "B"), k=10).schedules})
    assert keys[0] == keys[1]


def test_only_must_take_courses_are_scheduled():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Mon 10-12")]),
    )
    schedules = run(cat, must("A")).schedules
    assert [{ch.course for ch in s.choices} for s in schedules] == [{"A"}]


def test_must_take_unknown_course_is_an_error():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]))
    with pytest.raises(ValueError, match="XX999"):
        solve(cat, must("A", "XX999"))


def test_nothing_selected_is_an_error():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]))
    with pytest.raises(ValueError, match="no courses"):
        solve(cat, [])


def test_solve_returns_a_list_even_for_k_1():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")]))
    result = run(cat, must("A"), k=1)
    assert isinstance(result.schedules, list) and len(result.schedules) == 1


@pytest.mark.parametrize("k", [0, -1])
def test_k_below_1_is_an_error(k):
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12")]))
    with pytest.raises(ValueError):
        solve(cat, must("A"), k=k)
