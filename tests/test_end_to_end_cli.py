"""Real use through the CLI: the same scenarios as tests/test_end_to_end.py, typed as a user would type them.

Runs `python -m scheduler`'s code in-process on the realistic sample catalog, and checks both what's printed and the
schedules behind it (through the invariant checker). The exact counts are the ones documented in data/README.md.
"""
import itertools
import re

import pytest

from scheduler.catalog import load_catalog
from scheduler.models import DayOff, MustTake
from tests.helpers.cli_runner import PHRASE_CATALOG, SAMPLE, cli
from tests.helpers.fake_console import FakeConsole
from tests.helpers.fake_provider import FakeProvider, reply
from tests.helpers.invariants import assert_valid_results

pytestmark = pytest.mark.usefixtures("fresh_settings")

SAMPLE_CAT = load_catalog(SAMPLE)
REQUIRED = [c.id for c in SAMPLE_CAT.courses if c.required]
ELECTIVES = [c.id for c in SAMPLE_CAT.courses if not c.required]


def checked(run, k=5):
    o = run.outcome
    assert_valid_results(o.result, o.catalog, o.constraints, k)
    return run


def headers(out: str) -> list[str]:
    return re.findall(r"^Schedule \d+ of \d+", out, flags=re.M)


def courses_in(schedule) -> set[str]:
    return {ch.course for ch in schedule.choices}


# ---- the rules-only scenarios ----

def test_the_documented_feasible_phrase_gives_exactly_three_schedules():
    run = checked(cli(SAMPLE, "no classes before 10, keep 12-13 free", "--offline"))
    assert run.code == 0 and len(headers(run.out)) == 3
    assert "Only 3 possible schedules match your constraints." in run.out
    assert "Not understood" not in run.out


def test_fridays_off_conflicts_with_the_friday_only_required_course():
    run = checked(cli(SAMPLE, "fridays off", "--offline"))
    assert run.code == 1
    assert run.outcome.result.conflict == [MustTake(course="ZGC"), DayOff(day="Fri")]
    assert "ZGC" in run.out and "Fri off" in run.out


def test_dropping_the_friday_course_makes_fridays_off_work():
    run = checked(cli(SAMPLE, "fridays off", "--offline", "--drop", "ZGC"))
    assert run.code == 0 and run.outcome.result.schedules
    for s in run.outcome.result.schedules:
        assert all(session.day != "Fri" for ch in s.choices for session in ch.option.sessions)
    assert not re.search(r"^\s+Fri ", run.out, flags=re.M)


def test_the_full_manual_check_phrase_reports_the_ZGC_conflict():
    run = checked(cli(SAMPLE, "no classes before 10, fridays off, prefer Cohen, keep 12-13 free", "--offline"))
    assert run.code == 1 and "Not understood" not in run.out
    assert set(map(str, run.outcome.result.conflict)) == {str(MustTake(course="ZGC")), str(DayOff(day="Fri"))}


def test_a_realistic_soft_request_is_ranked_and_every_rule_is_printed_with_verbose():
    text = "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule"
    run = checked(cli(SAMPLE, text, "--offline", "--verbose"))
    constraints = run.outcome.constraints
    assert run.code == 0 and "Not understood" not in run.out
    for s in run.outcome.result.schedules:
        assert [item.constraint for item in s.breakdown] == constraints
    first = run.out.split("Schedule 1 of", 1)[1].split("Schedule 2 of", 1)[0]
    rule_lines = re.findall(r"^\s+(met|broken)\s", first, flags=re.M)
    assert len(rule_lines) == len(constraints)


def test_an_unparsed_clause_is_reported_and_the_rest_still_solves():
    run = checked(cli(SAMPLE, "no classes after 18, I'm not a morning person", "--offline"))
    assert run.code == 0
    assert [f.text for f in run.outcome.routed.result.unparsed] == ["I'm not a morning person"]
    assert "Not understood:" in run.out and "I'm not a morning person" in run.out


def test_picking_two_electives_with_preferences_takes_exactly_two_from_the_pool():
    run = checked(cli(SAMPLE, "prefer Cohen, avoid lecture 2 of XBI", "--offline", "--pick", "2:" + ",".join(ELECTIVES)))
    assert run.code == 0 and "Not understood" not in run.out
    for s in run.outcome.result.schedules:
        assert len(courses_in(s) & set(ELECTIVES)) == 2


def test_text_that_is_entirely_unparsed_still_schedules_the_required_courses():
    run = checked(cli(SAMPLE, "I'm not a morning person and I hate Mondays", "--offline"))
    assert run.code == 0 and run.outcome.routed.result.constraints == []
    assert all(courses_in(s) == set(REQUIRED) for s in run.outcome.result.schedules)


# ---- through the router, with an LLM set up (a fake one) ----

VAGUE = "no classes after 18, I'm not a morning person"


@pytest.fixture
def llm_set_up(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")
    return fresh_settings


def test_vague_text_is_read_by_the_llm_and_the_schedules_respect_it(llm_set_up):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"},
                               {"kind": "no_class_before", "time": "10:00", "strength": "soft"})])
    run = checked(cli(SAMPLE, VAGUE, provider=fake))
    assert run.outcome.routed.path == "llm" and len(fake.calls) == 1
    assert "Parser: llm" in run.out and "Not understood" not in run.out
    assert "no class before 10:00 (soft, weight 5)" in run.out


def test_the_same_vague_text_offline_uses_what_the_rules_read_and_reports_the_rest(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=none\n")
    run = checked(cli(SAMPLE, VAGUE))
    assert run.outcome.routed.path == "rules (offline)"
    assert "I'm not a morning person" in run.out and "python -m scheduler setup" in run.out


def test_clear_text_with_an_llm_set_up_makes_no_call_and_matches_the_rules_alone(llm_set_up):
    text = "ideally no classes before 10, avoid Brenner, prefer OLD tutorial 12, compact schedule"
    fake = FakeProvider([])
    run = checked(cli(SAMPLE, text, provider=fake))
    assert run.outcome.routed.path == "rules" and fake.calls == []
    offline = checked(cli(SAMPLE, text, "--offline"))
    assert run.outcome.result.schedules == offline.outcome.result.schedules


def test_a_course_the_llm_invents_is_reported_and_the_rest_still_solves(llm_set_up):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"}, {"kind": "must_take", "course": "MORNING101"})])
    run = checked(cli(SAMPLE, VAGUE, provider=fake))
    assert [f.reason for f in run.outcome.routed.result.unparsed] == ["unknown_name"]
    assert "MORNING101" in run.out and run.code == 0


def test_an_llm_pool_smaller_than_n_is_an_error_before_solving(llm_set_up):
    fake = FakeProvider([reply({"kind": "take_n_of", "courses": ELECTIVES[:2], "n": 3})])
    run = cli(SAMPLE, VAGUE, provider=fake)
    assert run.code == 2 and "can't take 3 from a pool of 2" in run.err


def test_the_same_llm_text_from_another_folder_is_served_from_the_shared_cache(llm_set_up, tmp_path, monkeypatch):
    fake = FakeProvider([reply({"kind": "no_class_after", "time": "18:00"})])
    assert cli(SAMPLE, VAGUE, provider=fake).outcome.routed.path == "llm"
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    run = checked(cli(SAMPLE, VAGUE, provider=fake))
    assert run.outcome.routed.path == "cache" and len(fake.calls) == 1


# ---- a review session ----

REVIEW_TEXT = "no classes before 10, fridays off, keep 12-13 free"


def test_review_deleting_fridays_off_gives_the_three_documented_schedules():
    console = FakeConsole(["d 2", ""])
    run = checked(cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", console=console))
    assert "2. Fri off (hard)" in run.out
    assert DayOff(day="Fri") not in run.outcome.constraints
    assert run.code == 0 and len(run.outcome.result.schedules) == 3


def test_review_making_fridays_off_soft_solves_and_shows_it_broken():
    console = FakeConsole(["h 2", ""])
    run = checked(cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", console=console))
    soft_fri = DayOff(day="Fri", strength="soft")
    assert soft_fri in run.outcome.constraints and run.code == 0
    for s in run.outcome.result.schedules:
        assert [i.met for i in s.breakdown if i.constraint == soft_fri] == [False]  # ZGC meets on Fridays
    assert re.search(r"Broken: Fri off \(soft, weight 5\): cost \d+", run.out)


def test_review_reprints_the_list_after_each_command():
    console = FakeConsole(["d 1", "w 3 8", ""])
    run = cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", console=console)
    assert run.out.count("3. keep 12:00-13:00 free") == 3
    assert "3. keep 12:00-13:00 free (hard, weight 8)" not in run.out  # hard rules don't show a weight
    assert run.outcome.constraints[-1].weight == 8


def test_review_edit_text_parses_again():
    console = FakeConsole(["t", "no classes before 10, keep 12-13 free", ""])
    run = checked(cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", console=console))
    assert len(run.outcome.result.schedules) == 3 and "replaced" in run.out


def test_review_quit_doesnt_solve():
    run = cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", console=FakeConsole(["q"]))
    assert run.code == 0 and run.outcome.result is None and headers(run.out) == []


def test_review_names_the_courses_being_scheduled():
    run = cli(SAMPLE, REVIEW_TEXT, "--review", "--offline", "--drop", "ZGC", console=FakeConsole(["q"]))
    line = next(l for l in run.out.splitlines() if l.startswith("Courses:"))
    assert all(c in line for c in REQUIRED if c != "ZGC") and "ZGC" not in line


# ---- generated combinations: both catalogs, every way of choosing courses, several texts ----

PHRASE_IDS = [c.id for c in load_catalog(PHRASE_CATALOG).courses]
SELECTIONS = {
    "required only": {SAMPLE: [], PHRASE_CATALOG: None},  # the phrase catalog marks nothing required
    "no-required + pick n": {SAMPLE: ["--no-required", "--pick", "2:" + ",".join(ELECTIVES[:4])],
                             PHRASE_CATALOG: ["--no-required", "--pick", "2:" + ",".join(PHRASE_IDS[:4])]},
    "pick all": {SAMPLE: ["--no-required", "--pick", ",".join(ELECTIVES[:2])],
                 PHRASE_CATALOG: ["--pick", ",".join(PHRASE_IDS[1:3])]},
    "required + pick": {SAMPLE: ["--pick", "1:" + ",".join(ELECTIVES[2:5])], PHRASE_CATALOG: None},
    "drop one": {SAMPLE: ["--drop", "ZGC,ECP"], PHRASE_CATALOG: None},
}
TEXTS = ["", "no classes before 10", "ideally fridays off, prefer Cohen", "compact schedule, keep 12-13 free",
         "I'm not a morning person"]
COMBOS = [(path, name, text, k)
          for path, name, text, k in itertools.product([SAMPLE, PHRASE_CATALOG], SELECTIONS, TEXTS, [1, 5])
          if SELECTIONS[name][path] is not None]


@pytest.mark.slow
@pytest.mark.parametrize("path, selection, text, k", COMBOS,
                         ids=[f"{p.stem}-{s}-{t[:20] or 'no text'}-top{k}" for p, s, t, k in COMBOS])
def test_every_combination_prints_exactly_the_valid_schedules_it_found(path, selection, text, k):
    argv = [path, *([text] if text else []), "--offline", "--top", k, *SELECTIONS[selection][path]]
    run = checked(cli(*argv), k=k)
    result = run.outcome.result
    assert run.code == (0 if result.schedules else 1)
    assert len(headers(run.out)) == len(result.schedules)
    if result.note:
        assert result.note in run.out
    for i, s in enumerate(result.schedules, 1):
        block = run.out.split(f"Schedule {i} of", 1)[1].split(f"Schedule {i + 1} of", 1)[0]
        for ch in s.choices:
            assert f"{ch.course} {ch.component} " in block
