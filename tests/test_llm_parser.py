"""LLMParser with a fake provider: the prompt, the flat output schema, validation with one retry, name checks, and
waiting out an overloaded provider."""
import json
import typing

import httpx
import pytest

from scheduler.models import Compact, Constraint, DayOff, MustTake, NoClassBefore, PreferGroup, PreferInstructor
from scheduler.parsing.base import ParseResult, PreferenceParser
from scheduler.parsing.llm import BUSY_WAITS, LLM_FIELD_NAMES, OUTPUT_SCHEMA, SYSTEM_PROMPT, LLMFailed, LLMParser, user_message
from tests.helpers.catalog_builder import catalog, course, grp
from tests.helpers.fake_provider import FakeProvider, reply

CONSTRAINT_MODELS = typing.get_args(typing.get_args(Constraint)[0])


@pytest.fixture
def cat():
    return catalog(
        course("CS101", "Intro to Programming",
               lecture=[grp("1", "Mon 10-12", instructor="Cohen", room="B-201")],
               lab=[grp("11", "Tue 14-16", instructor="Cohn"), grp("12", "Wed 14-16", instructor="Cohn")]),
        course("ALG", "Algorithms", lecture=[grp("1", "Sun 8-10", instructor="Cohen")]),
    )


def parse(cat, *replies, text="I'm not a morning person", budget=None):
    fake = FakeProvider(list(replies))
    return LLMParser(fake, budget=budget).parse(text, cat), fake


class Budget:
    """Allows `left` calls, then refuses."""

    def __init__(self, left):
        self.left = left
        self.spent = 0

    def try_spend(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        self.spent += 1
        return True


def test_llm_parser_is_a_preference_parser():
    assert isinstance(LLMParser(FakeProvider([])), PreferenceParser)


# ---- the prompt ----

def test_the_message_names_every_course_instructor_and_group(cat):
    msg = user_message("x", cat)
    for name in ["CS101", "Intro to Programming", "ALG", "Algorithms", "Cohen", "Cohn", "lecture", "lab", "11", "12"]:
        assert name in msg


def test_each_instructor_is_listed_once(cat):
    msg = user_message("x", cat)
    assert msg.count("Cohen") == 1 and msg.count("Cohn") == 1


def test_the_catalog_part_has_no_session_times_or_rooms(cat):
    # the fixed instructions may use times in their examples; the catalog listing never does
    listing = [line for line in user_message("x", cat).splitlines() if not line.startswith("Week:")]
    for detail in ["B-201", ":00", "10-12", "Mon", "Tue", "Sun"]:
        assert not any(detail in line for line in listing), detail


def test_the_message_states_the_catalogs_week(cat):
    week = catalog(course("A", lecture=[grp("1", "Mon 10-12")]), week=["Mon", "Tue", "Wed", "Thu", "Fri"])
    assert "Mon, Tue, Wed, Thu, Fri" in user_message("x", week)


@pytest.mark.parametrize("convention", [
    "lunch is 12:00-13:00",
    "weekend is fri and sat",
    "bare hour 1-7 means pm",
    "no strength word means hard",
    "is a must_take plus a hard prefer_group",  # "take CS101 lab 11"
])
def test_the_instructions_state_the_rule_parsers_conventions(convention):
    assert convention in " ".join(SYSTEM_PROMPT.lower().split())  # ignoring case and line breaks


def test_the_fixed_instructions_are_the_same_for_any_text_and_catalog(cat):
    other = catalog(course("X", lecture=[grp("1", "Sun 10-12", instructor="Levi")]))
    fake = FakeProvider([reply(), reply()])
    LLMParser(fake).parse("fridays off please", cat)
    LLMParser(fake).parse("something else", other)
    assert fake.calls[0]["system"] == fake.calls[1]["system"] == SYSTEM_PROMPT
    assert fake.calls[0]["schema"] == fake.calls[1]["schema"] == OUTPUT_SCHEMA


def test_the_users_text_comes_after_the_catalog(cat):
    msg = user_message("keep fridays free", cat)
    assert msg.index("CS101") < msg.index("keep fridays free")
    assert msg.rstrip().endswith("keep fridays free")


# ---- the flat output schema ----

def _item_schema():
    return OUTPUT_SCHEMA["properties"]["constraints"]["items"]


def test_the_schema_lists_exactly_the_constraint_kinds():
    kinds = {m.model_fields["kind"].default for m in CONSTRAINT_MODELS}
    assert set(_item_schema()["properties"]["kind"]["enum"]) == kinds


def test_the_schema_has_every_parameter_of_every_constraint_kind():
    params = {LLM_FIELD_NAMES.get(f, f) for m in CONSTRAINT_MODELS for f in m.model_fields}
    assert set(_item_schema()["properties"]) == params
    assert _item_schema()["required"] == ["kind", "strength"]


def test_the_instructor_parameter_has_an_unambiguous_name_for_the_model():
    # the live eval: a field called "name" was filled with the constraint's own name ("name": "prefer_group")
    assert "name" not in _item_schema()["properties"] and "instructor" in _item_schema()["properties"]
    assert "prefer_instructor(instructor)" in SYSTEM_PROMPT


@pytest.mark.parametrize("kind", ["prefer_instructor", "avoid_instructor"])
def test_the_instructor_parameter_maps_back_to_name(cat, kind):
    result, _ = parse(cat, reply({"kind": kind, "instructor": "Cohen", "strength": "soft"}))
    assert [(c.kind, c.name, c.strength) for c in result.constraints] == [(kind, "Cohen", "soft")]


@pytest.mark.parametrize("item, mentions", [
    ({"kind": "prefer_group", "course": "CS101", "component": "lab", "group": "11", "strength": "soft",
      "name": "prefer_group"}, "name"),
    ({"kind": "day_off", "day": "Fri", "strength": "hard", "instructor": "Cohen"}, "instructor"),
], ids=["name-on-a-group-rule", "instructor-on-another-kind"])
def test_a_stray_name_or_instructor_is_invalid_and_retried(cat, item, mentions):
    result, fake = parse(cat, {"constraints": [item], "unparsed": []}, reply({"kind": "day_off", "day": "Fri"}))
    assert len(fake.calls) == 2 and mentions in fake.calls[1]["user"][len(fake.calls[0]["user"]):]


def test_a_missing_strength_is_invalid_and_retried(cat):
    # the live eval: "prefer Cohen" came back without strength, which would silently have made it hard
    result, fake = parse(cat, {"constraints": [{"kind": "prefer_instructor", "instructor": "Cohen"}], "unparsed": []},
                         reply({"kind": "prefer_instructor", "instructor": "Cohen", "strength": "soft"}))
    assert result.constraints == [PreferInstructor(name="Cohen", strength="soft")]
    assert len(fake.calls) == 2 and "strength" in fake.calls[1]["user"][len(fake.calls[0]["user"]):]


def test_the_schema_stays_small():
    assert len(json.dumps(OUTPUT_SCHEMA)) < 3000


# ---- validation ----

def test_a_valid_reply_gives_validated_constraints_with_defaults_filled_in(cat):
    result, fake = parse(cat, reply({"kind": "day_off", "day": "Fri"}, {"kind": "compact"},
                                    {"kind": "no_class_before", "time": "10:00", "strength": "soft", "weight": 8}))
    assert isinstance(result, ParseResult)
    assert result.constraints == [DayOff(day="Fri"), Compact(), NoClassBefore(time="10:00", strength="soft", weight=8)]
    assert result.constraints[0].weight == 5  # weight is the one default the model leaves out
    assert result.constraints[1].strength == "soft"
    assert len(fake.calls) == 1


def test_null_parameters_are_treated_as_left_out(cat):
    result, _ = parse(cat, reply({"kind": "day_off", "day": "Fri", "time": None, "course": None}))
    assert result.constraints == [DayOff(day="Fri")]


def test_unparsed_fragments_from_the_model_reach_the_result_and_there_are_no_spans(cat):
    result, _ = parse(cat, reply({"kind": "day_off", "day": "Fri"}, unparsed=[("I hate mornings", "not_understood")]))
    assert [(f.text, f.reason, f.candidates) for f in result.unparsed] == [("I hate mornings", "not_understood", [])]
    assert result.spans == []


@pytest.mark.parametrize("bad, mentions", [
    ("not json {", "JSON"),
    ('["a", "list"]', "object"),
    ({"unparsed": []}, "constraints"),
    (reply({"kind": "lunch_break"}), "lunch_break"),
    (reply({"kind": "day_off", "day": "Fri", "time": "10:00"}), "time"),
    (reply({"kind": "day_off"}), "day"),
    (reply({"kind": "compact", "strength": "hard"}), "compact"),
    (reply(unparsed=[("x", "confused")]), "reason"),
], ids=["bad-json", "not-object", "no-constraints", "unknown-kind", "extra-param", "missing-param", "hard-compact",
        "bad-unparsed-reason"])
def test_an_invalid_reply_is_retried_once_with_the_error(cat, bad, mentions):
    result, fake = parse(cat, bad, reply({"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")]
    assert len(fake.calls) == 2
    retry = fake.calls[1]["user"]
    assert retry.startswith(fake.calls[0]["user"])  # same request, with the error added
    assert "invalid" in retry[len(fake.calls[0]["user"]):] and mentions in retry[len(fake.calls[0]["user"]):]


def test_two_invalid_replies_fail_without_a_third_call(cat):
    with pytest.raises(LLMFailed, match="invalid"):
        parse(cat, "nope", reply({"kind": "day_off"}))


def test_a_provider_error_fails_at_once_without_a_retry(cat):
    fake = FakeProvider([ConnectionError("network down"), reply()])
    with pytest.raises(LLMFailed, match="network down") as failed:
        LLMParser(fake).parse("x", cat)
    assert len(fake.calls) == 1
    assert failed.value.provider_error  # the live eval stops at the first one


def test_invalid_replies_are_not_a_provider_error(cat):
    with pytest.raises(LLMFailed) as failed:
        parse(cat, "nope", "nope again")
    assert not failed.value.provider_error


# ---- names are checked against the catalog ----

@pytest.mark.parametrize("bad, name", [
    ({"kind": "must_take", "course": "CS102"}, "CS102"),
    ({"kind": "take_n_of", "courses": ["CS101", "BIO9"], "n": 1}, "BIO9"),
    ({"kind": "avoid_instructor", "instructor": "Smith"}, "Smith"),
    ({"kind": "prefer_group", "course": "CS101", "component": "tutorial", "group": "1"}, "tutorial"),
    ({"kind": "prefer_group", "course": "CS101", "component": "lab", "group": "13"}, "13"),
], ids=["course", "course-in-pool", "instructor", "component", "group"])
def test_an_unknown_name_is_moved_to_unparsed_and_the_rest_is_kept(cat, bad, name):
    result, fake = parse(cat, reply(bad, {"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")]
    [frag] = result.unparsed
    assert frag.reason == "unknown_name" and name in frag.text
    assert len(fake.calls) == 1  # a wrong name is not a reason to pay for a retry


@pytest.mark.parametrize("bad, suggestion", [
    ({"kind": "must_take", "course": "CS102"}, "CS101"),
    ({"kind": "avoid_instructor", "instructor": "Cohenn"}, "Cohen"),
    ({"kind": "prefer_group", "course": "CS101", "component": "lab", "group": "13"}, "11"),
])
def test_an_unknown_name_suggests_what_the_catalog_has(cat, bad, suggestion):
    result, _ = parse(cat, reply(bad))
    assert suggestion in result.unparsed[0].candidates


@pytest.mark.parametrize("given, fixed", [
    ({"kind": "must_take", "course": "cs101"}, MustTake(course="CS101")),
    ({"kind": "prefer_instructor", "instructor": "cohen"}, PreferInstructor(name="Cohen")),
    ({"kind": "prefer_group", "course": "cs101", "component": "Lab", "group": "11"},
     PreferGroup(course="CS101", component="lab", group="11")),
])
def test_a_name_that_differs_only_in_case_gets_the_catalogs_spelling(cat, given, fixed):
    result, _ = parse(cat, reply(given))
    assert result.constraints == [fixed] and result.unparsed == []


def test_the_same_constraint_twice_is_kept_once(cat):
    result, _ = parse(cat, reply({"kind": "day_off", "day": "Fri"}, {"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")]


# ---- the daily limit ----

def test_every_call_is_spent_from_the_budget_retries_included(cat):
    budget = Budget(5)
    parse(cat, "bad", reply(), budget=budget)
    assert budget.spent == 2


def test_no_call_is_made_when_the_budget_is_used_up(cat):
    with pytest.raises(LLMFailed, match="daily limit"):
        parse(cat, reply(), budget=Budget(0))


def test_the_retry_is_skipped_when_the_first_call_used_the_last_of_the_budget(cat):
    fake = FakeProvider(["bad", reply()])
    with pytest.raises(LLMFailed, match="invalid.*daily limit"):
        LLMParser(fake, budget=Budget(1)).parse("x", cat)
    assert len(fake.calls) == 1


def test_an_exact_name_wins_over_one_that_differs_only_in_case():
    two = catalog(course("ab", lecture=[grp("1", "Sun 8-10")]), course("AB", lecture=[grp("1", "Mon 8-10")]))
    for given in ["ab", "AB"]:
        result, _ = parse(two, reply({"kind": "must_take", "course": given}))
        assert result.constraints == [MustTake(course=given)]


def test_a_constraint_that_isnt_an_object_is_retried(cat):
    result, fake = parse(cat, {"constraints": ["day_off"], "unparsed": []}, reply({"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")] and len(fake.calls) == 2


# ---- property: whatever valid constraints a model sends, the parser returns exactly those ----

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from scheduler.parsing.llm import CONSTRAINT_PARAMS  # noqa: E402
from tests.helpers.strategies import problems  # noqa: E402

NAME_FIELDS = ("course", "component", "instructor")


def _as_sent(c, pad_nulls: bool, recase) -> dict:
    """A constraint the way a model might send it: defaults left out, maybe every other parameter as null,
    and names in whatever case."""
    item = c.model_dump(mode="json", exclude_defaults=True)
    item["kind"], item["strength"] = c.kind, c.strength  # strength is always stated
    if "name" in item:
        item["instructor"] = item.pop("name")
    for f in NAME_FIELDS:
        if f in item:
            item[f] = recase(item[f])
    if "courses" in item:
        item["courses"] = [recase(x) for x in item["courses"]]
    if pad_nulls:
        item.update({p: None for p in CONSTRAINT_PARAMS if p not in item})
    return item


@given(problems(), st.booleans(), st.sampled_from([str, str.lower, str.upper, str.swapcase]))
def test_valid_constraints_come_back_unchanged(problem, pad_nulls, recase):
    cat, constraints = problem
    ids = [c.id for c in cat.courses]
    if len({i.lower() for i in ids}) < len(ids) and recase is not str:
        recase = str  # ids that differ only in case can't be told apart once recased
    result, fake = parse(cat, reply(*(_as_sent(c, pad_nulls, recase) for c in constraints)))
    unique = list({c.model_dump_json(): c for c in constraints}.values())
    assert result.constraints == unique
    assert result.unparsed == [] and len(fake.calls) == 1


def test_a_case_insensitive_match_to_several_names_is_ambiguous():
    two = catalog(course("ab", lecture=[grp("1", "Sun 8-10")]), course("AB", lecture=[grp("1", "Mon 8-10")]))
    result, _ = parse(two, reply({"kind": "must_take", "course": "Ab"}))
    assert result.constraints == []
    [frag] = result.unparsed
    assert frag.reason == "ambiguous_name" and sorted(frag.candidates) == ["AB", "ab"]


# ---- an overloaded provider (HTTP 503) is waited out ----

class StatusError(Exception):
    """An SDK error carrying an HTTP status the way anthropic and openai do."""

    def __init__(self, status):
        super().__init__(f"{status} error")
        self.status_code = status


def parse_waiting(cat, *replies, budget=None):
    fake, waits = FakeProvider(list(replies)), []
    result = LLMParser(fake, budget=budget, sleep=waits.append).parse("x", cat)
    return result, fake, waits


def test_a_503_is_waited_out_and_the_call_made_again(cat):
    result, fake, waits = parse_waiting(cat, StatusError(503), StatusError(503), reply({"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")]
    assert len(fake.calls) == 3 and waits == list(BUSY_WAITS[:2])
    assert fake.calls[1]["user"] == fake.calls[0]["user"]  # the same request, not the invalid-reply retry


def test_a_503_that_outlasts_the_waits_is_a_provider_error(cat):
    fake, waits = FakeProvider([StatusError(503)] * (len(BUSY_WAITS) + 1) + [reply()]), []
    with pytest.raises(LLMFailed, match="503") as failed:
        LLMParser(fake, sleep=waits.append).parse("x", cat)
    assert failed.value.provider_error
    assert waits == list(BUSY_WAITS) and len(fake.calls) == len(BUSY_WAITS) + 1


@pytest.mark.parametrize("status", [400, 404, 429, 500])
def test_other_http_errors_fail_at_once_without_waiting(cat, status):
    fake, waits = FakeProvider([StatusError(status), reply()]), []
    with pytest.raises(LLMFailed) as failed:
        LLMParser(fake, sleep=waits.append).parse("x", cat)
    assert failed.value.provider_error and waits == [] and len(fake.calls) == 1


def test_each_try_after_a_503_is_spent_from_the_budget(cat):
    budget = Budget(5)
    parse_waiting(cat, StatusError(503), reply(), budget=budget)
    assert budget.spent == 2


def test_a_503_with_no_budget_left_fails_without_waiting(cat):
    fake, waits = FakeProvider([StatusError(503), reply()]), []
    with pytest.raises(LLMFailed, match="503") as failed:
        LLMParser(fake, budget=Budget(1), sleep=waits.append).parse("x", cat)
    assert failed.value.provider_error and waits == [] and len(fake.calls) == 1


def test_a_503_on_the_invalid_reply_retry_is_waited_out_too(cat):
    result, fake, waits = parse_waiting(cat, "bad", StatusError(503), reply({"kind": "day_off", "day": "Fri"}))
    assert result.constraints == [DayOff(day="Fri")] and waits == [BUSY_WAITS[0]]
    assert "invalid" in fake.calls[2]["user"]  # the retry still carries the error


def _genai_503():
    errors = pytest.importorskip("google.genai.errors")
    return errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


def _httpx_503():
    request = httpx.Request("POST", "http://box:11434/api/chat")
    return httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))


@pytest.mark.parametrize("make", [_genai_503, _httpx_503, lambda: StatusError(503)], ids=["gemini", "ollama", "anthropic-openai"])
def test_the_503_is_recognised_in_each_sdk_error_shape(cat, make):
    result, _, waits = parse_waiting(cat, make(), reply())
    assert waits == [BUSY_WAITS[0]]
