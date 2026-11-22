"""Real use through the web API: the same scenarios as tests/test_end_to_end.py, sent the way the page sends them
(catalog -> parse -> solve), on the realistic sample catalog. Every result goes through the invariant checker.
"""
import itertools

import pytest

from scheduler.models import DayOff, MustTake
from tests.helpers.api_client import api, ok, parse, phrase_catalog, rules_of, sample, solve
from tests.helpers.fake_provider import FakeProvider, reply

pytestmark = pytest.mark.usefixtures("fresh_settings")

SAMPLE = sample()
REQUIRED = [c["id"] for c in SAMPLE["courses"] if c.get("required")]
ELECTIVES = [c["id"] for c in SAMPLE["courses"] if not c.get("required")]


@pytest.fixture
def c():
    return api()


def courses_in(schedule: dict) -> set[str]:
    return {s["course"] for s in schedule["sessions"]}


def schedule_once(c, text, modes=None, pick_n=None, top=5):
    parsed = parse(c, SAMPLE, text)
    return parsed, solve(c, SAMPLE, parsed["rules"], modes=modes, pick_n=pick_n, top=top)


# ---- the rules-only scenarios ----

def test_the_documented_feasible_phrase_gives_exactly_three_schedules(c):
    parsed, j = schedule_once(c, "no classes before 10, keep 12-13 free")
    assert parsed["unparsed"] == [] and len(j["schedules"]) == 3
    assert j["note"] == "Only 3 possible schedules match your constraints."


def test_fridays_off_conflicts_with_the_friday_only_required_course(c):
    _, j = schedule_once(c, "fridays off")
    assert j["result"]["conflict"] == [MustTake(course="ZGC").model_dump(mode="json"),
                                       DayOff(day="Fri").model_dump(mode="json")]


def test_switching_the_friday_course_off_in_the_picker_makes_fridays_off_work(c):
    _, j = schedule_once(c, "fridays off", modes={"ZGC": "off"})
    assert j["schedules"] and all(s["day"] != "Fri" for sch in j["schedules"] for s in sch["sessions"])


def test_the_full_manual_check_phrase_reports_the_ZGC_conflict(c):
    parsed, j = schedule_once(c, "no classes before 10, fridays off, prefer Cohen, keep 12-13 free")
    assert parsed["unparsed"] == [] and j["conflict"] == ["must take ZGC", "Fri off"]


def test_a_realistic_soft_request_is_ranked_and_every_rule_is_listed(c):
    parsed, j = schedule_once(c, "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule")
    assert parsed["unparsed"] == [] and j["schedules"]
    for sch in j["schedules"]:
        assert len(sch["rules"]) == len(j["constraints"])


def test_an_unparsed_clause_is_reported_and_the_rest_still_solves(c):
    parsed, j = schedule_once(c, "no classes after 18, I'm not a morning person")
    assert [u["text"] for u in parsed["unparsed"]] == ["I'm not a morning person"] and j["schedules"]


def test_picking_two_electives_with_preferences_takes_exactly_two_from_the_pool(c):
    _, j = schedule_once(c, "prefer Cohen, avoid lecture 2 of XBI", modes={e: "pool" for e in ELECTIVES}, pick_n=2)
    assert all(len(courses_in(s) & set(ELECTIVES)) == 2 for s in j["schedules"])


def test_text_that_is_entirely_unparsed_still_schedules_the_required_courses(c):
    parsed, j = schedule_once(c, "I'm not a morning person and I hate Mondays")
    assert parsed["rules"] == [] and all(courses_in(s) == set(REQUIRED) for s in j["schedules"])


# ---- through the router, with an LLM set up (a fake one) ----

VAGUE = "no classes after 18, I'm not a morning person"


@pytest.fixture
def llm_set_up(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")


def test_vague_text_is_read_by_the_llm_and_the_schedules_respect_it(llm_set_up):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"},
                               {"kind": "no_class_before", "time": "10:00", "strength": "soft"})])
    c = api(provider=fake)
    parsed = parse(c, SAMPLE, VAGUE)
    assert parsed["path"] == "llm" and parsed["unparsed"] == [] and len(fake.calls) == 1
    assert solve(c, SAMPLE, parsed["rules"])["schedules"]


def test_the_same_vague_text_offline_uses_what_the_rules_read_and_reports_the_rest(fresh_settings, c):
    fresh_settings.save("LLM_PROVIDER=none\n")
    parsed = parse(c, SAMPLE, VAGUE)
    assert parsed["path"] == "rules (offline)" and [u["text"] for u in parsed["unparsed"]] == ["I'm not a morning person"]


def test_clear_text_with_an_llm_set_up_makes_no_call_and_matches_the_rules_alone(llm_set_up):
    text = "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule"
    fake = FakeProvider([])
    c = api(provider=fake)
    parsed = parse(c, SAMPLE, text)
    assert parsed["path"] == "rules" and fake.calls == []
    assert solve(c, SAMPLE, parsed["rules"])["result"] == solve(api(), SAMPLE, parse(api(), SAMPLE, text)["rules"])["result"]


def test_the_offline_switch_skips_the_llm_even_when_one_is_set_up(llm_set_up):
    fake = FakeProvider([])
    c = api(provider=fake)
    parsed = parse(c, SAMPLE, VAGUE, offline=True)
    assert fake.calls == [] and parsed["path"] == "rules (offline)" and parsed["offline"] is True
    assert [u["text"] for u in parsed["unparsed"]] == ["I'm not a morning person"]
    assert "add an LLM" not in parsed["path_label"]  # one is set up; the user chose offline for now
    assert solve(c, SAMPLE, parsed["rules"])["schedules"]


def test_the_offline_switch_note_points_back_to_the_switch_not_to_setup(llm_set_up):
    note = parse(api(provider=FakeProvider([])), SAMPLE, VAGUE, offline=True)["note"]
    assert "your choice" in note and "Switch the parser back to LLM" in note and "Set up an LLM" not in note


def test_offline_without_an_llm_still_suggests_setup(fresh_settings, c):
    fresh_settings.save("LLM_PROVIDER=none\n")
    note = parse(c, SAMPLE, VAGUE, offline=True)["note"]
    assert "Set up an LLM" in note and "your choice" not in note


def test_the_offline_switch_saves_nothing(llm_set_up, fresh_settings):
    before = fresh_settings.saved()
    parse(api(provider=FakeProvider([])), SAMPLE, VAGUE, offline=True)
    assert fresh_settings.saved() == before
    assert ok(api().get("/api/setup"))["provider"] == "gemini"


def test_switching_back_to_the_llm_uses_it_again(llm_set_up):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"})])
    c = api(provider=fake)
    parse(c, SAMPLE, VAGUE, offline=True)
    assert parse(c, SAMPLE, VAGUE)["path"] == "llm" and len(fake.calls) == 1


def test_a_course_the_llm_invents_is_reported_and_the_rest_still_solves(llm_set_up):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"}, {"kind": "must_take", "course": "MORNING101"})])
    c = api(provider=fake)
    parsed = parse(c, SAMPLE, VAGUE)
    assert [u["reason"] for u in parsed["unparsed"]] == ["unknown_name"]
    assert solve(c, SAMPLE, parsed["rules"])["schedules"]


def test_an_llm_pool_smaller_than_n_is_an_error_before_solving(llm_set_up):
    fake = FakeProvider([reply({"kind": "take_n_of", "courses": ELECTIVES[:2], "n": 3})])
    c = api(provider=fake)
    parsed = parse(c, SAMPLE, VAGUE)
    r = c.post("/api/solve", json={"catalog": SAMPLE, "constraints": rules_of(parsed)})
    assert r.status_code == 400 and "can't take 3 from a pool of 2" in r.json()["error"]


# ---- a review session: edit the chips, add a phrase, then solve ----

REVIEW_TEXT = "no classes before 10, fridays off, keep 12-13 free"


def test_review_deleting_fridays_off_gives_the_three_documented_schedules(c):
    rules = [r for r in rules_of(parse(c, SAMPLE, REVIEW_TEXT)) if r["kind"] != "day_off"]
    assert len(solve(c, SAMPLE, rules)["schedules"]) == 3


def test_review_making_fridays_off_soft_solves_and_shows_it_broken(c):
    rules = rules_of(parse(c, SAMPLE, REVIEW_TEXT))
    for r in rules:
        if r["kind"] == "day_off":
            r["strength"] = "soft"
    edited = ok(c.post("/api/rules", json={"constraints": rules}))
    assert "Fri off (soft, weight 5)" in [r["text"] for r in edited["rules"]]
    j = solve(c, SAMPLE, rules)
    assert j["schedules"]
    for s in j["schedules"]:  # ZGC meets on Fridays, so every schedule breaks it
        assert [r["met"] for r in s["rules"] if r["text"] == "Fri off (soft, weight 5)"] == [False]


def test_review_adding_a_phrase_joins_its_rules_to_the_list(c):
    rules = rules_of(parse(c, SAMPLE, "no classes before 10"))
    added = rules_of(parse(c, SAMPLE, "compact schedule"))
    j = solve(c, SAMPLE, rules + added, modes={"ZGC": "off"})
    assert [x["kind"] for x in j["constraints"]][-2:] == ["no_class_before", "compact"]


# ---- generated combinations: both catalogs, every way of choosing courses, several texts ----

PHRASE = phrase_catalog()
PHRASE_IDS = [x["id"] for x in PHRASE["courses"]]
SELECTIONS = {
    "required only": {"sample": ({}, None), "phrase": None},
    "required + pool": {"sample": ({e: "pool" for e in ELECTIVES[2:5]}, 1), "phrase": None},
    "pool with n": {"sample": ({**{r: "off" for r in REQUIRED}, **{e: "pool" for e in ELECTIVES[:4]}}, 2),
                    "phrase": ({i: "pool" for i in PHRASE_IDS[:4]}, 2)},
    "pool takes all": {"sample": ({**{r: "off" for r in REQUIRED}, **{e: "pool" for e in ELECTIVES[:2]}}, None),
                       "phrase": ({i: "pool" for i in PHRASE_IDS[1:3]}, None)},
    "one required off": {"sample": ({"ZGC": "off"}, None), "phrase": None},
}
TEXTS = ["", "no classes before 10", "ideally fridays off, prefer Cohen", "compact schedule, keep 12-13 free",
         "I'm not a morning person"]
CATS = {"sample": SAMPLE, "phrase": PHRASE}
COMBOS = [(cat, sel, text, k) for cat, sel, text, k in itertools.product(CATS, SELECTIONS, TEXTS, [1, 5])
          if SELECTIONS[sel][cat] is not None]


@pytest.mark.slow
@pytest.mark.parametrize("cat, selection, text, k", COMBOS,
                         ids=[f"{c}-{s}-{t[:20] or 'no text'}-top{k}" for c, s, t, k in COMBOS])
def test_every_combination_returns_valid_schedules_with_text_for_every_choice(cat, selection, text, k):
    c = api()
    modes, pick_n = SELECTIONS[selection][cat]
    j = solve(c, CATS[cat], parse(c, CATS[cat], text)["rules"], modes=modes, pick_n=pick_n, top=k)
    assert len(j["schedules"]) == len(j["result"]["schedules"])
    for view, raw in zip(j["schedules"], j["result"]["schedules"]):
        for ch in raw["choices"]:
            assert any(s["course"] == ch["course"] and s["component"] == ch["component"] for s in view["sessions"])


# ---- exports: every rank of a real request, exported and read back ----

@pytest.mark.parametrize("text, modes, pick_n", [
    ("no classes before 10, keep 12-13 free", None, None),
    ("ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule", None, None),
    ("prefer Cohen, avoid lecture 2 of XBI", {e: "pool" for e in ELECTIVES}, 2),
])
def test_every_rank_exports_to_json_that_reads_back_as_a_valid_schedule(c, text, modes, pick_n):
    from scheduler.catalog import parse_catalog
    from scheduler.models import Schedule, parse_constraint
    from tests.helpers.invariants import assert_valid_schedule

    _, j = schedule_once(c, text, modes=modes, pick_n=pick_n)
    assert j["schedules"]
    for rank, raw in enumerate(j["result"]["schedules"], 1):
        body = {"catalog": SAMPLE, "schedule": raw, "best": j["result"]["schedules"][0],
                "constraints": j["constraints"], "courses": j["courses"], "rank": rank}
        data = ok(c.post("/api/export/json", json=body))
        schedule = Schedule.model_validate(data["schedule"])
        assert schedule == Schedule.model_validate(raw) and data["view"] == j["schedules"][rank - 1]
        assert_valid_schedule(schedule, parse_catalog(data["catalog"]), [parse_constraint(x) for x in data["constraints"]])
        assert c.post("/api/export/xlsx", json=body).status_code == 200
