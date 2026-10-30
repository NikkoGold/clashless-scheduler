import copy
import json

import pytest

from scheduler.catalog import CatalogError, load_catalog, merge_options, overlaps, parse_catalog
from scheduler.constraints import default_constraints
from scheduler.models import MustTake, Session
from tests.helpers.catalog_builder import catalog, course, grp

VALID = catalog(
    course("CS101", lecture=[grp("1", "Sun 10-12", instructor="Cohen")],
                    lab=[grp("11", "Mon 10-11", parent="1"), grp("12", "Tue 10-11", parent="1")]),
    raw=True,
)


def test_valid_catalog_loads_from_file(tmp_path):
    path = tmp_path / "courses.json"
    path.write_text(json.dumps(VALID))
    cat = load_catalog(path)
    assert [c.id for c in cat.courses] == ["CS101"]
    assert set(cat.courses[0].components) == {"lecture", "lab"}


def test_session_times_are_parsed_to_minutes():
    s = Session.model_validate({"day": "Sun", "start": "10:00", "end": "12:00"})
    assert (s.start, s.end) == (600, 720)


def _edit(fn):
    data = copy.deepcopy(VALID)
    fn(data)
    return data


def _lecture(data):
    return data["courses"][0]["components"]["lecture"][0]


def _set_session(**fields):
    return lambda d: _lecture(d)["sessions"][0].update(fields)


# (description, edit to VALID, fragments the error message must contain)
INVALID = [
    ("bad day", _set_session(day="Mnd"), ["CS101", "Mnd"]),
    ("end before start", _set_session(start="12:00", end="10:00"), ["CS101", "end"]),
    ("end equals start", _set_session(start="10:00", end="10:00"), ["CS101", "end"]),
    ("not on the hour", _set_session(start="10:30"), ["CS101", "10:30"]),
    ("bad time format", _set_session(start="ten"), ["CS101", "ten"]),
    ("day outside week", _set_session(day="Sat"), ["CS101", "Sat"]),
    ("unknown field", lambda d: _lecture(d).update(instrutor="Cohen"), ["instrutor"]),
    ("missing sessions", lambda d: _lecture(d).pop("sessions"), ["CS101", "sessions"]),
    ("empty sessions", lambda d: _lecture(d).update(sessions=[]), ["CS101", "sessions"]),
    ("no components", lambda d: d["courses"][0].update(components={}), ["CS101", "component"]),
    ("duplicate course id", lambda d: d["courses"].append(copy.deepcopy(d["courses"][0])), ["CS101", "duplicate"]),
    ("duplicate group in a component",
     lambda d: d["courses"][0]["components"]["lab"].append(grp("11", "Wed 10-11")), ["CS101", "lab", "11", "duplicate"]),
    ("group overlaps itself",
     lambda d: _lecture(d)["sessions"].append({"day": "Sun", "start": "11:00", "end": "13:00"}), ["CS101", "overlap"]),
    ("unknown week day", lambda d: d.update(week=["Sun", "Funday"]), ["Funday"]),
]


@pytest.mark.parametrize("edit,fragments", [(e, f) for _, e, f in INVALID], ids=[n for n, _, _ in INVALID])
def test_invalid_catalog_gives_readable_error(edit, fragments):
    with pytest.raises(CatalogError) as err:
        parse_catalog(_edit(edit))
    message = str(err.value).lower()
    for fragment in fragments:
        assert fragment.lower() in message


def test_load_error_names_the_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(_edit(_set_session(day="Mnd"))))
    with pytest.raises(CatalogError, match="broken.json"):
        load_catalog(path)


def test_same_group_id_in_different_components_is_allowed():
    cat = catalog(course("CS101", tutorial=[grp("11", "Sun 10-11")], lab=[grp("11", "Mon 10-11")]))
    assert set(cat.courses[0].components) == {"tutorial", "lab"}


def test_unknown_parent_is_allowed_and_stored():
    cat = catalog(course("CS101", lecture=[grp("1", "Sun 10-12")], lab=[grp("91", "Mon 10-11", parent="9")]))
    assert cat.courses[0].components["lab"][0].parent == "9"


def test_week_defaults_to_sun_to_fri():
    assert parse_catalog(VALID).week == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]


def test_custom_week_is_respected():
    cat = catalog(course("CS101", lecture=[grp("1", "Sat 10-12")]), week=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
    assert cat.week[-1] == "Sat"


def test_attendance_required_defaults_to_true():
    assert parse_catalog(VALID).courses[0].components["lecture"][0].attendance_required is True


def test_attendance_required_false_is_accepted():
    cat = catalog(course("CS101", lecture=[grp("1", "Sun 10-12", attendance_required=False)]))
    assert cat.courses[0].components["lecture"][0].attendance_required is False


def test_required_defaults_to_false():
    assert parse_catalog(VALID).courses[0].required is False


def test_fractional_credits_are_accepted():
    assert catalog(course("CS101", credits=1.5, lecture=[grp("1", "Sun 10-12")])).courses[0].credits == 1.5


def test_default_constraints_make_each_required_course_a_hard_must_take():
    cat = catalog(
        course("REQ1", required=True, lecture=[grp("1", "Sun 10-12")]),
        course("ELEC", lecture=[grp("1", "Mon 10-12")]),
        course("REQ2", required=True, lecture=[grp("1", "Tue 10-12")]),
    )
    assert default_constraints(cat) == [MustTake(course="REQ1"), MustTake(course="REQ2")]
    assert all(c.strength == "hard" for c in default_constraints(cat))


def _session(text):
    day, times = text.split()
    start, end = times.split("-")
    return Session.model_validate({"day": day, "start": f"{start}:00", "end": f"{end}:00"})


@pytest.mark.parametrize("a,b,expected", [
    ("Sun 10-12", "Sun 12-14", False),  # back-to-back
    ("Sun 12-14", "Sun 10-12", False),
    ("Sun 10-12", "Sun 11-13", True),
    ("Sun 10-14", "Sun 11-12", True),   # one inside the other
    ("Sun 10-12", "Sun 10-12", True),
    ("Sun 10-12", "Mon 10-12", False),  # different day
])
def test_overlap(a, b, expected):
    assert overlaps(_session(a), _session(b)) is expected


def test_groups_with_identical_sessions_merge_into_one_option():
    c = catalog(course("CS101", lab=[
        grp("11", "Mon 10-12", instructor="Cohen", room="L1"),
        grp("12", "Mon 10-12", instructor="Levi", room="L2"),
        grp("13", "Tue 10-12"),
    ])).courses[0]
    options = merge_options(c)["lab"]
    assert sorted(o.group_ids for o in options) == [("11", "12"), ("13",)]
    merged = next(o for o in options if len(o.group_ids) == 2)
    assert [g.instructor for g in merged.groups] == ["Cohen", "Levi"]


def test_merge_ignores_session_order():
    c = catalog(course("CS101", lecture=[
        grp("1", "Mon 10-12", "Wed 10-12"),
        grp("2", "Wed 10-12", "Mon 10-12"),
    ])).courses[0]
    assert [o.group_ids for o in merge_options(c)["lecture"]] == [("1", "2")]


def test_groups_sharing_only_some_sessions_stay_separate():
    c = catalog(course("CS101", lecture=[
        grp("1", "Mon 10-12", "Wed 10-12"),
        grp("2", "Mon 10-12", "Thu 10-12"),
    ])).courses[0]
    assert len(merge_options(c)["lecture"]) == 2
