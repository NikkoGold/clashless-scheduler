"""The web API: catalog, parse, rules and solve. Errors are always {"error": ...} with a 4xx status.

The display text comes from scheduler/present.py, so the page says exactly what the CLI says.
"""
import pytest

import scheduler.solver as solver_module
from tests.helpers.api_client import api, ok, parse, phrase_catalog, rules_of, sample, solve
from tests.helpers.catalog_builder import catalog, course, grp
from tests.test_solver_time_limit import LATE, StepClock

pytestmark = pytest.mark.usefixtures("fresh_settings")


@pytest.fixture
def c():
    return api()


def five_choices():
    return catalog(course("A", required=True, lecture=[grp(str(i), f"Sun {8 + 2 * i}-{9 + 2 * i}") for i in range(5)]),
                   raw=True)


def merged():
    return catalog(course(
        "M", required=True,
        lecture=[grp("1", "Sun 8-10", instructor="Cohen", room="R1")],
        lab=[grp("11", "Mon 10-12", instructor="Adler", room="L1"),
             grp("12", "Mon 10-12", instructor="Brenner", room="L2"),
             grp("21", "Tue 10-12", instructor="Gal", room="L3")]), raw=True)


def pool_catalog():
    return catalog(course("R", required=True, lecture=[grp("1", "Sun 8-10")]),
                   course("P1", lecture=[grp("1", "Mon 8-10")]),
                   course("P2", lecture=[grp("1", "Tue 8-10")]), raw=True)


# ---- the page and the sample ----

def test_the_page_is_served(c):
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_the_sample_catalog_is_served(c):
    assert ok(c.get("/api/sample")) == sample()


# ---- catalog ----

def test_a_valid_catalog_returns_its_courses_week_and_term(c):
    j = ok(c.post("/api/catalog", json={"catalog": sample()}))
    assert j["term"] == sample()["term"] and j["week"] == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    ecp = next(x for x in j["courses"] if x["id"] == "ECP")
    assert ecp["name"] == "Emergency Command Procedures" and ecp["required"] is True and ecp["credits"] == 2
    pc2 = next(x for x in j["courses"] if x["id"] == "PC2")
    assert len(pc2["instructors"]) == len(set(pc2["instructors"])) and "Brenner" in pc2["instructors"]


@pytest.mark.parametrize("bad, says", [
    (catalog(course("A", lecture=[grp("1", "Mnd 8-10")]), raw=True), "Mnd"),
    (catalog(course("A", lecture=[grp("1", "Sun 8-10")]), course("A", lecture=[grp("1", "Mon 8-10")]), raw=True),
     "duplicate course id A"),
    ({"term": "T"}, "courses"),
    (["not", "an", "object"], "catalog"),
])
def test_an_invalid_catalog_gets_a_readable_400(c, bad, says):
    r = c.post("/api/catalog", json={"catalog": bad})
    assert r.status_code in (400, 422) and says in r.json()["error"]


# ---- parse ----

def test_parse_returns_rules_unparsed_advice_path_and_spans(c):
    j = parse(c, sample(), "no classes before 10, I'm not a morning person")
    assert [r["text"] for r in j["rules"]] == ["no class before 10:00 (hard)"]
    assert j["rules"][0]["constraint"] == {"kind": "no_class_before", "time": "10:00", "days": None,
                                           "strength": "hard", "weight": 5}
    [u] = j["unparsed"]
    assert u["text"] == "I'm not a morning person" and u["reason"] == "not_understood"
    assert u["message"].startswith('"I\'m not a morning person": not understood')
    assert j["path"] == "rules (offline)" and j["path_label"].startswith("rules (offline)")
    assert j["spans"] == [{"text": "no classes before 10", "rules": [0]}]


def test_empty_text_gives_no_rules(c):
    j = parse(c, sample(), "   ")
    assert j["rules"] == [] and j["unparsed"] == []


def test_offline_unparsed_text_points_to_the_setup_button_not_the_command(c):
    note = parse(c, sample(), "I'm not a morning person")["note"]
    assert "Setup" in note and "python -m scheduler" not in note


def test_no_parse_text_names_the_cli_setup_command(fresh_settings, c):
    fresh_settings.save("LLM_PROVIDER=none\n")
    j = parse(c, sample(), "I'm not a morning person, no classes before 10")
    text = " ".join([j["path_label"], j["note"] or ""] + [u["message"] for u in j["unparsed"]])
    assert "python -m scheduler" not in text and "Setup" in text


def test_each_course_lists_its_groups_with_instructor_and_times(c):
    j = ok(c.post("/api/catalog", json={"catalog": sample()}))
    old = next(x for x in j["courses"] if x["id"] == "OLD")
    assert {"component": "lecture", "group": "1", "instructor": "Cohen",
            "times": "Mon 10:00-12:00, Wed 10:00-12:00"} in old["groups"]
    nav = next(x for x in j["courses"] if x["id"] == "NAV2")
    assert any(g["instructor"] is None for g in nav["groups"])
    assert len(old["groups"]) == sum(len(gs) for gs in next(x for x in sample()["courses"] if x["id"] == "OLD")["components"].values())


def test_an_unclear_or_carries_its_advice(c):
    [u] = parse(c, sample(), "drop XBI or HEP")["unparsed"]
    assert u["reason"] == "unclear_or" and '"or" is unclear here' in u["message"]


# ---- rules (chip edits) ----

def test_edited_rules_come_back_with_new_descriptions(c):
    rules = rules_of(parse(c, sample(), "no classes before 10, fridays off"))
    rules[1]["strength"], rules[1]["weight"] = "soft", 8
    j = ok(c.post("/api/rules", json={"constraints": rules}))
    assert [r["text"] for r in j["rules"]] == ["no class before 10:00 (hard)", "Fri off (soft, weight 8)"]


@pytest.mark.parametrize("bad, says", [
    ({"kind": "compact", "strength": "hard"}, "compact"),
    ({"kind": "day_off", "day": "Fri", "weight": 11}, "weight"),
    ({"kind": "lunch_break"}, "lunch_break"),
])
def test_an_invalid_edit_is_refused(c, bad, says):
    r = c.post("/api/rules", json={"constraints": [{"kind": "day_off", "day": "Mon"}, bad]})
    assert r.status_code == 400 and says in r.json()["error"] and "rule 2" in r.json()["error"]


# ---- solve ----

def test_solve_returns_k_results_with_display_text(c):
    j = solve(c, five_choices())
    assert len(j["schedules"]) == len(j["result"]["schedules"]) == 5
    assert j["courses"] == "A (required)"


@pytest.mark.parametrize("top", [0, 21, "five"])
def test_top_outside_one_to_twenty_is_refused(c, top):
    r = c.post("/api/solve", json={"catalog": five_choices(), "constraints": [], "top": top})
    assert r.status_code in (400, 422) and "top" in r.json()["error"]


def test_fewer_than_k_says_only_n_possible(c):
    assert solve(c, five_choices(), top=8)["note"] == "Only 5 possible schedules match your constraints."


@pytest.mark.parametrize("modes, pick_n, says", [
    ({"R": "off"}, None, "no courses to schedule"),
    ({"P1": "pool", "P2": "pool"}, 3, "can't take 3 from a pool of 2"),
])
def test_an_impossible_course_choice_is_a_400(c, modes, pick_n, says):
    r = c.post("/api/solve", json={"catalog": pool_catalog(), "constraints": [], "modes": modes, "pick_n": pick_n})
    assert r.status_code == 400 and says in r.json()["error"]


@pytest.mark.parametrize("rule, says", [
    ({"kind": "must_take", "course": "NOPE"}, "NOPE"),
    ({"kind": "prefer_instructor", "name": "Nobody", "strength": "soft"}, "Nobody"),
    ({"kind": "take_n_of", "courses": ["P1", "P2"], "n": 3}, "pool of 2"),
])
def test_a_rule_naming_something_the_catalog_lacks_is_a_400(c, rule, says):
    r = c.post("/api/solve", json={"catalog": pool_catalog(), "constraints": [rule]})
    assert r.status_code == 400 and says in r.json()["error"]


def test_a_conflict_is_a_result_not_an_error(c):
    j = solve(c, sample(), rules_of(parse(c, sample(), "fridays off")))
    assert j["schedules"] == [] and j["conflict"] == ["must take ZGC", "Fri off"]
    assert j["note"] == "No schedule works: must take ZGC + Fri off conflict."


def test_nothing_to_schedule_is_reported(c):
    j = solve(c, pool_catalog(), [{"kind": "exclude_course", "course": "R"}])
    assert j["schedules"] == [] and j["result"]["conflict"] and "No schedule works" in j["note"]


def test_the_time_limit_with_results_keeps_them(c, monkeypatch):
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, 0, 0, LATE))
    j = solve(c, five_choices())
    assert len(j["schedules"]) == 2 and j["timed_out"] is True
    assert j["note"] == "Stopped after 3 minutes: showing the 2 found; there may be more."


def test_the_time_limit_with_nothing_found_says_so(c, monkeypatch):
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, LATE))
    j = solve(c, five_choices())
    assert j["schedules"] == [] and j["timed_out"] is True and "Stopped after 3 minutes" in j["note"]


# ---- display text ----

def test_each_session_has_day_time_label_rooms_and_instructors(c):
    j = solve(c, merged())
    sessions = j["schedules"][0]["sessions"]
    assert {"day": "Sun", "start": "08:00", "end": "10:00", "course": "M", "component": "lecture",
            "label": "M lecture 1", "rooms": "R1", "instructors": "Cohen"} in sessions


def test_a_merged_option_shows_both_members(c):
    j = solve(c, merged())
    labs = [s for sch in j["schedules"] for s in sch["sessions"] if s["label"] == "M lab 11 or 12"]
    assert labs and labs[0]["rooms"] == "L1 or L2" and labs[0]["instructors"] == "Adler or Brenner"


def test_the_member_to_register_for_is_shown_when_a_preference_picks_one(c):
    j = solve(c, merged(), rules_of(parse(c, merged(), "prefer Adler")))
    assert any(s["label"] == "M lab 11 or 12 (register for 11)" for s in j["schedules"][0]["sessions"])


def test_tie_notes_are_shown(c):
    j = solve(c, merged(), rules_of(parse(c, merged(), "prefer Adler, prefer Brenner")))
    notes = [n for s in j["schedules"] for n in s["notes"]]
    assert notes and all(n.startswith("Register for") for n in notes)


def test_every_rule_is_listed_met_or_broken_with_its_cost(c):
    j = solve(c, merged(), rules_of(parse(c, merged(), "prefer Gal")))
    first, second = j["schedules"][0], j["schedules"][1]
    assert first["rules"] == [{"text": "must take M (hard)", "met": True, "cost": 0},
                              {"text": "prefer Gal (soft, weight 5)", "met": True, "cost": 0}]
    assert {"text": "prefer Gal (soft, weight 5)", "met": False, "cost": 5} in second["rules"]


def test_later_results_say_how_they_differ_from_the_first(c):
    j = solve(c, merged())
    assert j["schedules"][0]["differs"] == []
    assert len(j["schedules"][1]["differs"]) == 1 and "instead of" in j["schedules"][1]["differs"][0]


def test_the_constraints_solved_with_are_returned(c):
    j = solve(c, pool_catalog(), [{"kind": "day_off", "day": "Fri"}], modes={"P1": "pool"})
    assert [x["kind"] for x in j["constraints"]] == ["must_take", "take_n_of", "day_off"]


def test_the_phrase_catalog_works_with_a_pool(c):
    ids = [x["id"] for x in phrase_catalog()["courses"]][:3]
    assert solve(c, phrase_catalog(), modes={i: "pool" for i in ids}, pick_n=2)["schedules"]


# ---- errors are always readable ----

@pytest.mark.parametrize("route, body", [
    ("/api/parse", {"text": "x"}),            # no catalog
    ("/api/solve", {"catalog": {}}),          # an empty catalog
    ("/api/rules", {"constraints": "nope"}),
    ("/api/setup", {}),
])
def test_missing_or_wrong_fields_are_a_readable_4xx(c, route, body):
    r = c.post(route, json=body)
    assert 400 <= r.status_code < 500 and isinstance(r.json()["error"], str) and r.json()["error"]


def test_malformed_json_is_a_readable_4xx(c):
    r = c.post("/api/parse", content=b"{not json", headers={"content-type": "application/json"})
    assert 400 <= r.status_code < 500 and r.json()["error"]


def test_the_course_summary_names_each_course_with_its_id_in_parentheses():
    c = api()
    j = solve(c, sample(), modes={"ZGC": "off"})
    assert j["courses"].startswith("Emergency Command Procedures (ECP), ")
    assert "ZGC" not in j["courses"] and j["courses"].endswith(" (required)")


def test_the_course_lines_give_the_required_courses_and_the_pool_separately():
    c = api()
    j = solve(c, sample(), modes={"HEP": "pool", "XBI": "pool"}, pick_n=1)
    assert len(j["course_lines"]) == 2
    assert j["course_lines"][0].endswith("(required)") and j["course_lines"][1].startswith("1 of ")
    assert "; ".join(j["course_lines"]) == j["courses"]
