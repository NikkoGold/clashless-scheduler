"""Choosing courses (required / in pool / off, pick N): one function shared by the CLI flags and the web picker."""
import pytest

from scheduler.models import MustTake, TakeNOf
from scheduler.selection import SelectionError, default_modes, select_courses
from tests.helpers.catalog_builder import catalog, course, grp


@pytest.fixture
def cat():
    # R1 and R2 are required; P1-P3 are electives
    return catalog(
        course("R1", required=True, lecture=[grp("1", "Sun 8-10")]),
        course("R2", required=True, lecture=[grp("1", "Mon 8-10")]),
        course("P1", lecture=[grp("1", "Tue 8-10")]),
        course("P2", lecture=[grp("1", "Wed 8-10")]),
        course("P3", lecture=[grp("1", "Thu 8-10")]),
    )


def test_by_default_required_courses_are_required_and_the_rest_are_off(cat):
    assert default_modes(cat) == {"R1": "required", "R2": "required", "P1": "off", "P2": "off", "P3": "off"}


def test_required_courses_become_hard_must_takes(cat):
    s = select_courses(cat, default_modes(cat))
    assert s.required == ["R1", "R2"] and s.pool == []
    assert s.constraints == [MustTake(course="R1"), MustTake(course="R2")]


def test_a_pool_with_n_takes_n(cat):
    s = select_courses(cat, {**default_modes(cat), "P1": "pool", "P2": "pool", "P3": "pool"}, pick_n=2)
    assert s.constraints[-1] == TakeNOf(courses=["P1", "P2", "P3"], n=2) and s.n == 2


def test_a_pool_without_n_takes_all_of_it(cat):
    s = select_courses(cat, {**default_modes(cat), "P1": "pool", "P3": "pool"})
    assert s.constraints[-1] == TakeNOf(courses=["P1", "P3"], n=2)


def test_a_required_course_in_the_pool_stops_being_required(cat):
    s = select_courses(cat, {**default_modes(cat), "R1": "pool", "P1": "pool"}, pick_n=1)
    assert s.constraints == [MustTake(course="R2"), TakeNOf(courses=["R1", "P1"], n=1)]


def test_courses_switched_off_are_left_out(cat):
    s = select_courses(cat, {**default_modes(cat), "R2": "off"})
    assert s.constraints == [MustTake(course="R1")]


def test_courses_missing_from_the_modes_use_their_default(cat):
    assert select_courses(cat, {"P1": "pool"}).required == ["R1", "R2"]


def test_the_pool_keeps_catalog_order(cat):
    assert select_courses(cat, {"P3": "pool", "P1": "pool"}).pool == ["P1", "P3"]


@pytest.mark.parametrize("modes, pick_n, says", [
    ({"R1": "off", "R2": "off"}, None, "no courses to schedule"),
    ({"P1": "pool", "P2": "pool"}, 3, "can't take 3 from a pool of 2"),
    ({"P1": "pool"}, 0, "at least 1"),
    ({"NOPE": "pool"}, None, "NOPE"),
    ({"P1": "maybe"}, None, "maybe"),
])
def test_impossible_selections_are_refused_with_a_reason(cat, modes, pick_n, says):
    with pytest.raises(SelectionError, match=says):
        select_courses(cat, modes, pick_n)


def test_no_courses_says_whether_the_catalog_marks_any_required():
    none_required = catalog(course("A", lecture=[grp("1", "Sun 8-10")]))
    with pytest.raises(SelectionError, match="marks no course as required"):
        select_courses(none_required, default_modes(none_required))


# ---- the summary with full course names (the page and the exports) ----

def test_the_summary_can_show_full_names_with_the_id_in_parentheses():
    from scheduler.selection import Selection
    s = Selection(required=["ECP"], pool=["HEP", "XBI"], n=1)
    names = {"ECP": "Emergency Command Procedures", "HEP": "Hull Engineering", "XBI": "Xenobiology"}
    assert s.summary(names) == ("Emergency Command Procedures (ECP) (required); "
                                "1 of Hull Engineering (HEP), Xenobiology (XBI) (pool)")


def test_a_name_that_is_just_the_id_isnt_repeated():
    from scheduler.selection import Selection
    assert Selection(required=["A"]).summary({"A": "A"}) == "A (required)"


def test_without_names_the_summary_keeps_the_ids():
    from scheduler.selection import Selection
    assert Selection(required=["ECP"], pool=["HEP"], n=1).summary() == "ECP (required); 1 of HEP (pool)"


def test_the_summary_parts_put_the_required_courses_and_the_pool_on_their_own_lines():
    from scheduler.selection import Selection
    s = Selection(required=["ECP"], pool=["HEP", "XBI"], n=1)
    names = {"ECP": "Emergency Command Procedures", "HEP": "Hull Engineering", "XBI": "Xenobiology"}
    assert s.summary_parts(names) == ["Emergency Command Procedures (ECP) (required)",
                                      "1 of Hull Engineering (HEP), Xenobiology (XBI) (pool)"]
    assert s.summary(names) == "; ".join(s.summary_parts(names))


@pytest.mark.parametrize("selection, count", [({"required": ["A"]}, 1), ({"pool": ["A", "B"], "n": 1}, 1),
                                              ({"required": ["A"], "pool": ["B"], "n": 1}, 2)])
def test_there_is_a_part_only_for_what_was_chosen(selection, count):
    from scheduler.selection import Selection
    assert len(Selection(**selection).summary_parts()) == count
