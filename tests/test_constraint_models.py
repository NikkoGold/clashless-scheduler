import pytest
from pydantic import ValidationError

from scheduler.models import parse_constraint

VALID = [
    {"kind": "must_take", "course": "A"},
    {"kind": "exclude_course", "course": "A"},
    {"kind": "take_n_of", "courses": ["A", "B"], "n": 1},
    {"kind": "no_class_before", "time": "10:00"},
    {"kind": "no_class_before", "time": "10:00", "days": ["Mon", "Tue"]},
    {"kind": "no_class_after", "time": "16:00"},
    {"kind": "no_class_after", "time": "16:00", "days": ["Fri"]},
    {"kind": "day_off", "day": "Fri"},
    {"kind": "max_days", "n": 4},
    {"kind": "max_hours_per_day", "h": 6},
    {"kind": "free_block", "start": "12:00", "end": "13:00"},
    {"kind": "free_block", "day": "Sun", "start": "12:00", "end": "13:00"},
    {"kind": "max_gap", "minutes": 60},
    {"kind": "compact", "strength": "soft"},
    {"kind": "prefer_group", "course": "A", "component": "lab", "group": "11"},
    {"kind": "avoid_group", "course": "A", "component": "lab", "group": "11"},
    {"kind": "prefer_instructor", "name": "Cohen"},
    {"kind": "avoid_instructor", "name": "Cohen"},
    {"kind": "min_credits", "n": 12},
    {"kind": "max_credits", "n": 20.5},
]


@pytest.mark.parametrize("data", VALID, ids=lambda d: d["kind"])
def test_each_constraint_kind_is_parsed_from_json(data):
    assert parse_constraint(data).kind == data["kind"]


@pytest.mark.parametrize("data", VALID, ids=lambda d: d["kind"])
def test_each_constraint_kind_round_trips_through_json(data):
    constraint = parse_constraint(data)
    assert parse_constraint(constraint.model_dump(mode="json")) == constraint


INVALID = {
    "weight too low": {"kind": "day_off", "day": "Fri", "weight": 0},
    "weight too high": {"kind": "day_off", "day": "Fri", "weight": 11},
    "bad strength": {"kind": "day_off", "day": "Fri", "strength": "maybe"},
    "bad day": {"kind": "day_off", "day": "Funday"},
    "bad day in list": {"kind": "no_class_before", "time": "10:00", "days": ["Funday"]},
    "empty day list": {"kind": "no_class_before", "time": "10:00", "days": []},
    "bad time": {"kind": "no_class_before", "time": "25:00"},
    "free block start after end": {"kind": "free_block", "start": "13:00", "end": "12:00"},
    "free block start equals end": {"kind": "free_block", "start": "12:00", "end": "12:00"},
    "take_n_of n below 1": {"kind": "take_n_of", "courses": ["A"], "n": 0},
    "take_n_of empty pool": {"kind": "take_n_of", "courses": [], "n": 1},
    "take_n_of duplicate in pool": {"kind": "take_n_of", "courses": ["A", "A"], "n": 1},
    "max_days zero": {"kind": "max_days", "n": 0},
    "max_days above a week": {"kind": "max_days", "n": 8},
    "max_hours_per_day zero": {"kind": "max_hours_per_day", "h": 0},
    "negative max_gap": {"kind": "max_gap", "minutes": -1},
    "negative min_credits": {"kind": "min_credits", "n": -1},
    "negative max_credits": {"kind": "max_credits", "n": -1},
    "unknown kind": {"kind": "no_tuesdays"},
    "extra field": {"kind": "day_off", "day": "Fri", "colour": "red"},
    "hard compact": {"kind": "compact", "strength": "hard"},
}


@pytest.mark.parametrize("data", INVALID.values(), ids=INVALID.keys())
def test_invalid_constraint_is_rejected(data):
    with pytest.raises(ValidationError):
        parse_constraint(data)


def test_constraint_times_need_not_be_on_the_hour():
    assert parse_constraint({"kind": "no_class_before", "time": "10:30"}).time == 630


def test_compact_defaults_to_soft():
    assert parse_constraint({"kind": "compact"}).strength == "soft"


def test_other_kinds_default_to_hard_with_weight_5():
    constraint = parse_constraint({"kind": "day_off", "day": "Fri"})
    assert (constraint.strength, constraint.weight) == ("hard", 5)
