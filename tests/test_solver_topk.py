from scheduler.solver import solve
from tests.helpers.catalog_builder import catalog, course, grp, must
from tests.helpers.invariants import assert_valid_results, schedule_key
from tests.helpers.oracle import assert_matches_oracle


def run(cat, constraints, k=5):
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    assert_matches_oracle(result, cat, constraints, k)
    return result


def test_top_k_results_are_all_different():
    # 3 lectures x 3 labs, no clashes: 9 schedules, ask for 5
    cat = catalog(course("A",
        lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12"), grp("3", "Tue 10-12")],
        lab=[grp("11", "Wed 10-12"), grp("12", "Thu 10-12"), grp("13", "Fri 10-12")],
    ))
    schedules = run(cat, must("A"), k=5).schedules
    assert len({schedule_key(s) for s in schedules}) == 5


def test_exactly_k_possible_schedules_returns_k_without_a_note():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")]))
    result = run(cat, must("A"), k=2)
    assert len(result.schedules) == 2 and result.note is None


def test_only_two_possible_schedules_with_k_5_returns_two_and_says_so():
    # A and B each meet Sun or Mon 10-12 and can't share a day: (A Sun, B Mon) or (A Mon, B Sun)
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")]),
        course("B", lecture=[grp("1", "Sun 10-12"), grp("2", "Mon 10-12")]),
    )
    result = run(cat, must("A", "B"), k=5)
    assert len(result.schedules) == 2
    assert "only 2 possible" in result.note.lower()


def test_identical_labs_are_one_option_so_results_never_repeat_on_the_grid():
    # labs 11 and 12 meet at the same time: 2 visible choices (11-or-12, 13), not 3
    cat = catalog(course("A",
        lecture=[grp("1", "Sun 10-12")],
        lab=[grp("11", "Mon 10-12", room="L1"), grp("12", "Mon 10-12", room="L2"), grp("13", "Tue 10-12")],
    ))
    result = run(cat, must("A"), k=5)
    labs = sorted(ch.option.group_ids for s in result.schedules for ch in s.choices if ch.component == "lab")
    assert labs == [("11", "12"), ("13",)]
    assert "only 2 possible" in result.note.lower()


def test_infeasible_returns_an_empty_list():
    cat = catalog(
        course("A", lecture=[grp("1", "Sun 10-12")]),
        course("B", lecture=[grp("1", "Sun 10-12")]),
    )
    assert run(cat, must("A", "B")).schedules == []
