"""Real use, end to end: the user's text -> RuleParser -> the picker's defaults -> solver -> results.

Runs on the realistic sample catalog (data/sample_courses.json). The exact counts asserted here are
the ones documented in data/README.md (checked by brute force when the file was written).
Later steps add the same scenarios through their own interface: the CLI (step 5) and the API (step 6).
"""
from pathlib import Path

import pytest

from scheduler.catalog import load_catalog
from scheduler.constraints import default_constraints
from scheduler.models import DayOff, MustTake, TakeNOf
from scheduler.parsing.rules import RuleParser
from scheduler.solver import solve
from tests.helpers.invariants import assert_valid_results

SAMPLE = Path(__file__).parent.parent / "data" / "sample_courses.json"


@pytest.fixture(scope="module")
def cat():
    return load_catalog(SAMPLE)


def run(cat, text, k=5, switched_off=(), extra=()):
    """What the app does on "Schedule": parse, start from the required courses (minus any the user
    switched off in the picker), add the parsed rules, solve, and check the results."""
    parsed = RuleParser().parse(text, cat)
    defaults = [c for c in default_constraints(cat) if c.course not in switched_off]
    constraints = defaults + list(extra) + parsed.constraints
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    return parsed, constraints, result


def chosen_courses(schedule) -> set[str]:
    return {ch.course for ch in schedule.choices}


def test_the_documented_feasible_phrase_gives_exactly_three_schedules(cat):
    parsed, _, result = run(cat, "no classes before 10, keep 12-13 free")
    assert parsed.unparsed == []
    assert len(result.schedules) == 3
    assert result.note == "Only 3 possible schedules match your constraints."


def test_fridays_off_conflicts_with_the_friday_only_required_course(cat):
    _, _, result = run(cat, "fridays off")
    assert result.schedules == []
    assert result.conflict == [MustTake(course="ZGC"), DayOff(day="Fri")]
    assert "ZGC" in result.note and "Fri" in result.note


def test_switching_the_friday_course_off_in_the_picker_makes_fridays_off_work(cat):
    _, _, result = run(cat, "fridays off", switched_off={"ZGC"})
    assert result.schedules
    for s in result.schedules:
        assert all(session.day != "Fri" for ch in s.choices for session in ch.option.sessions)


def test_the_full_manual_check_phrase_reports_the_ZGC_conflict(cat):
    parsed, _, result = run(cat, "no classes before 10, fridays off, prefer Cohen, keep 12-13 free")
    assert parsed.unparsed == []
    assert result.schedules == []
    assert set(map(str, result.conflict)) == {str(MustTake(course="ZGC")), str(DayOff(day="Fri"))}


def test_a_realistic_soft_request_is_ranked_and_fully_explained(cat):
    text = "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule"
    parsed, constraints, result = run(cat, text)
    assert parsed.unparsed == []
    assert len(parsed.constraints) == 4 and all(c.strength == "soft" for c in parsed.constraints)
    assert result.schedules
    for s in result.schedules:
        # every rule, the parsed ones included, is in the breakdown: the user sees what they're accepting
        assert [item.constraint for item in s.breakdown] == constraints


def test_an_unparsed_clause_is_reported_and_the_rest_still_solves(cat):
    parsed, _, result = run(cat, "no classes after 18, I'm not a morning person")
    assert [(f.text, f.reason) for f in parsed.unparsed] == [("I'm not a morning person", "not_understood")]
    assert [c.kind for c in parsed.constraints] == ["no_class_after"]
    assert result.schedules


def test_picking_two_electives_with_preferences_takes_exactly_two_from_the_pool(cat):
    electives = [c.id for c in cat.courses if not c.required]
    pool = TakeNOf(courses=electives, n=2)
    parsed, _, result = run(cat, "prefer Cohen, avoid lecture 2 of XBI", extra=[pool])
    assert parsed.unparsed == []
    assert result.schedules
    for s in result.schedules:
        assert len(chosen_courses(s) & set(electives)) == 2


def test_text_that_is_entirely_unparsed_still_schedules_the_required_courses(cat):
    parsed, constraints, result = run(cat, "I'm not a morning person and I hate Mondays")
    assert parsed.constraints == []
    assert [f.reason for f in parsed.unparsed] == ["not_understood", "not_understood"]
    assert constraints == default_constraints(cat)
    assert result.schedules
    required = {c.id for c in cat.courses if c.required}
    assert all(chosen_courses(s) == required for s in result.schedules)


# ---- through the router (step 4): rules first, the LLM only for what they can't read ----

from scheduler.config import Config  # noqa: E402
from scheduler.parsing.router import Router  # noqa: E402
from tests.helpers.fake_provider import FakeProvider, reply  # noqa: E402

GEMINI = Config(provider="gemini", model="gemini-flash-lite-latest", api_key="k")


def run_routed(cat, text, tmp_path, replies=(), config=GEMINI, k=5):
    """The same as run(), with the router choosing the parser. Returns the routed parse and the fake provider."""
    fake = FakeProvider(list(replies))
    routed = Router(config, cache_dir=tmp_path / ".cache", provider_factory=lambda cfg: fake).parse(text, cat)
    constraints = default_constraints(cat) + routed.result.constraints
    result = solve(cat, constraints, k=k)
    assert_valid_results(result, cat, constraints, k)
    return routed, constraints, result, fake


VAGUE = "no classes after 18, I'm not a morning person"


def test_vague_text_is_read_by_the_llm_and_the_schedules_respect_it(cat, tmp_path):
    llm = reply({"kind": "no_class_after", "time": "18:00"}, {"kind": "no_class_before", "time": "10:00", "strength": "soft"})
    routed, constraints, result, fake = run_routed(cat, VAGUE, tmp_path, [llm])
    assert routed.path == "llm" and len(fake.calls) == 1
    assert routed.result.unparsed == []
    assert result.schedules
    for s in result.schedules:
        # the LLM's soft rule is in the breakdown, met or broken: the user sees what they're accepting
        assert [item.constraint for item in s.breakdown] == constraints


def test_the_same_vague_text_offline_uses_what_the_rules_read_and_reports_the_rest(cat, tmp_path):
    routed, _, result, fake = run_routed(cat, VAGUE, tmp_path, config=Config(provider="none"))
    assert routed.path == "rules (offline)" and routed.note
    assert [f.text for f in routed.result.unparsed] == ["I'm not a morning person"]
    assert [c.kind for c in routed.result.constraints] == ["no_class_after"]
    assert result.schedules and fake.calls == []


def test_clear_text_with_an_llm_set_up_makes_no_call_and_matches_the_rules_alone(cat, tmp_path):
    text = "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule"
    routed, _, result, fake = run_routed(cat, text, tmp_path)
    assert routed.path == "rules" and fake.calls == []
    _, _, rules_only = run(cat, text)
    assert result.schedules == rules_only.schedules


def test_a_course_the_llm_invents_is_reported_and_the_rest_still_solves(cat, tmp_path):
    llm = reply({"kind": "no_class_after", "time": "18:00"}, {"kind": "must_take", "course": "MORNING101"})
    routed, _, result, _ = run_routed(cat, VAGUE, tmp_path, [llm])
    assert [f.reason for f in routed.result.unparsed] == ["unknown_name"]
    assert [c.kind for c in routed.result.constraints] == ["no_class_after"]
    assert result.schedules


def test_an_llm_pool_smaller_than_n_is_kept_and_reported_as_an_error_before_solving(cat, tmp_path):
    # same rule as the picker: "take 3 of 2 courses" can never be met, so the user is told and rephrases
    pool = [c.id for c in cat.courses if not c.required][:2]
    fake = FakeProvider([reply({"kind": "take_n_of", "courses": pool, "n": 3})])
    routed = Router(GEMINI, cache_dir=tmp_path / ".cache", provider_factory=lambda cfg: fake).parse(VAGUE, cat)
    assert [c.kind for c in routed.result.constraints] == ["take_n_of"]  # not silently dropped
    with pytest.raises(ValueError, match=r"can't take 3 from a pool of 2"):
        solve(cat, default_constraints(cat) + routed.result.constraints, k=5)
