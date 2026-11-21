"""The CLI: flags, what it prints, and exit codes (0 schedules found, 1 none or a conflict, 2 usage or input errors).

Runs in-process (tests/helpers/cli_runner.py) with each test's own empty settings folders. The schedules behind the
printed text go through the invariant checker wherever a solve happens. See plan.md, CLI.
"""
import re

import pytest

import scheduler.solver as solver_module
from scheduler.catalog import load_catalog
from scheduler.models import MustTake, TakeNOf
from scheduler.parsing.base import UnparsedFragment
from scheduler.present import unparsed_message
from tests.helpers.catalog_builder import catalog, course, grp
from tests.helpers.cli_runner import PHRASE_CATALOG, SAMPLE, cli, write_catalog
from tests.helpers.fake_console import FakeConsole
from tests.helpers.fake_provider import FakeProvider, reply
from tests.helpers.invariants import assert_valid_results
from tests.test_solver_time_limit import LATE, StepClock

pytestmark = pytest.mark.usefixtures("fresh_settings")


def five_choices(tmp_path):
    # A is required and has 5 lecture groups: exactly 5 schedules
    return write_catalog(tmp_path, catalog(
        course("A", required=True, lecture=[grp(str(i), f"Sun {8 + 2 * i}-{9 + 2 * i}") for i in range(5)]), raw=True))


def pool_catalog(tmp_path):
    # R is required; P1-P3 are electives; nothing clashes
    return write_catalog(tmp_path, catalog(
        course("R", required=True, lecture=[grp("1", "Sun 8-10")]),
        course("P1", lecture=[grp("1", "Mon 8-10")]),
        course("P2", lecture=[grp("1", "Tue 8-10")]),
        course("P3", lecture=[grp("1", "Wed 8-10")]),
        raw=True))


def merged_catalog(tmp_path):
    # M's labs 11 and 12 meet at identical times (one option, "11 or 12"); lab 21 is the other option
    return write_catalog(tmp_path, catalog(course(
        "M", required=True,
        lecture=[grp("1", "Sun 8-10", instructor="Cohen", room="R1")],
        lab=[grp("11", "Mon 10-12", instructor="Adler", room="L1"),
             grp("12", "Mon 10-12", instructor="Brenner", room="L2"),
             grp("21", "Tue 10-12", instructor="Gal", room="L3")]), raw=True))


def checked(run, k=5):
    """The run's schedules pass the invariant checker; returns the run."""
    o = run.outcome
    assert_valid_results(o.result, o.catalog, o.constraints, k)
    return run


def headers(out: str) -> list[str]:
    return re.findall(r"^Schedule \d+ of \d+", out, flags=re.M)


# ---- how many results ----

def test_a_basic_run_prints_k_schedules(tmp_path):
    run = checked(cli(five_choices(tmp_path), "--offline"))
    assert run.code == 0
    assert headers(run.out) == [f"Schedule {i} of 5" for i in range(1, 6)]


@pytest.mark.parametrize("k", [1, 3, 20])
def test_top_n_changes_how_many_are_shown(tmp_path, k):
    run = checked(cli(five_choices(tmp_path), "--offline", "--top", k), k=k)
    assert len(headers(run.out)) == min(k, 5) == len(run.outcome.result.schedules)


@pytest.mark.parametrize("k", ["0", "21", "-1", "five"])
def test_top_outside_one_to_twenty_is_refused(tmp_path, k):
    run = cli(five_choices(tmp_path), "--offline", "--top", k)
    assert run.code == 2
    assert "1" in run.err and "20" in run.err
    assert run.outcome.result is None


def test_fewer_than_k_prints_only_n_possible(tmp_path):
    run = checked(cli(five_choices(tmp_path), "--offline", "--top", 8), k=8)
    assert "Only 5 possible schedules match your constraints." in run.out


# ---- which courses ----

def test_required_courses_are_scheduled_by_default(tmp_path):
    run = checked(cli(pool_catalog(tmp_path), "--offline"))
    assert run.outcome.constraints == [MustTake(course="R")]
    assert "Courses: R (required)" in run.out


def test_pick_n_takes_exactly_n_from_the_pool(tmp_path):
    run = checked(cli(pool_catalog(tmp_path), "--offline", "--pick", "2:P1,P2,P3"))
    assert TakeNOf(courses=["P1", "P2", "P3"], n=2) in run.outcome.constraints
    for s in run.outcome.result.schedules:
        assert len({ch.course for ch in s.choices} & {"P1", "P2", "P3"}) == 2
    assert "2 of P1, P2, P3" in run.out


def test_pick_without_n_takes_all_of_them(tmp_path):
    run = checked(cli(pool_catalog(tmp_path), "--offline", "--pick", "P1,P3"))
    assert TakeNOf(courses=["P1", "P3"], n=2) in run.outcome.constraints


def test_course_ids_in_pick_and_drop_match_the_catalog_ignoring_case(tmp_path):
    run = checked(cli(pool_catalog(tmp_path), "--offline", "--pick", "1:p1, p2"))
    assert TakeNOf(courses=["P1", "P2"], n=1) in run.outcome.constraints


@pytest.mark.parametrize("pick, says", [
    ("4:P1,P2,P3", "pool of 3"),
    ("0:P1,P2", "at least 1"),
    ("2:P1,NOPE", "NOPE"),
    ("P1,P1", "twice"),
    ("2:", "course"),
    ("x:P1", "x"),
])
def test_a_bad_pick_is_refused_before_solving(tmp_path, pick, says):
    run = cli(pool_catalog(tmp_path), "--offline", "--pick", pick)
    assert run.code == 2 and says in run.err
    assert run.outcome.result is None


def test_an_unknown_course_suggests_a_close_one(tmp_path):
    run = cli(SAMPLE, "--offline", "--pick", "GVQ")
    assert run.code == 2 and "GVO" in run.err


def test_no_required_drops_every_required_course(tmp_path):
    run = checked(cli(pool_catalog(tmp_path), "--offline", "--no-required", "--pick", "P1"))
    assert run.outcome.constraints == [TakeNOf(courses=["P1"], n=1)]


def test_drop_switches_off_one_required_course():
    run = checked(cli(SAMPLE, "--offline", "--drop", "ZGC"))
    required = {c.id for c in load_catalog(SAMPLE).courses if c.required}
    assert {c.course for c in run.outcome.constraints if c.kind == "must_take"} == required - {"ZGC"}


@pytest.mark.parametrize("drop, says", [("NOPE", "NOPE"), ("HEP", "isn't required")])
def test_dropping_an_unknown_or_non_required_course_is_refused(drop, says):
    run = cli(SAMPLE, "--offline", "--drop", drop)
    assert run.code == 2 and says in run.err


def test_a_course_in_both_drop_and_pick_is_refused():
    run = cli(SAMPLE, "--offline", "--drop", "ZGC", "--pick", "ZGC,HEP")
    assert run.code == 2 and "ZGC" in run.err


def test_a_required_course_in_pick_moves_into_the_pool(tmp_path):
    # like the picker's three-way toggle: required, in pool, or off
    run = checked(cli(pool_catalog(tmp_path), "--offline", "--pick", "1:R,P1"))
    assert run.outcome.constraints == [TakeNOf(courses=["R", "P1"], n=1)]


@pytest.mark.parametrize("argv", [(), ("--no-required",)])
def test_no_courses_at_all_points_to_pick(argv):
    path = PHRASE_CATALOG if not argv else SAMPLE  # the phrase catalog marks nothing required
    run = cli(path, "--offline", *argv)
    assert run.code == 2 and "--pick" in run.err
    assert run.outcome.result is None


# ---- the parser and settings are always shown ----

def test_the_parser_path_and_settings_source_are_printed(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=none\n")
    run = cli(five_choices(tmp_path), "no classes before 10")
    assert "Parser: rules (offline)" in run.out
    assert f"Settings: {fresh_settings.user_file.resolve()}" in run.out


def test_settings_from_the_environment_are_named_but_never_their_values(fresh_settings, tmp_path, monkeypatch):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=file-key\n")
    monkeypatch.setenv("GEMINI_API_KEY", "env-secret")
    run = cli(five_choices(tmp_path), "no classes before 10")
    assert "GEMINI_API_KEY" in run.out and "from the environment" in run.out
    assert "env-secret" not in run.out + run.err and "file-key" not in run.out + run.err


def test_a_project_env_shows_as_the_settings_source(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")
    fresh_settings.save("LLM_PROVIDER=none\n", project=True)
    run = cli(five_choices(tmp_path), "no classes before 10")
    assert f"Settings: {fresh_settings.project_env.resolve()}" in run.out
    assert "Parser: rules (offline)" in run.out


def test_offline_never_calls_the_llm_even_when_one_is_set_up_and_saves_nothing(fresh_settings, tmp_path):
    before = fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n").read_text(encoding="utf-8")
    fake = FakeProvider([])
    run = checked(cli(five_choices(tmp_path), "no classes after 18, I'm not a morning person", "--offline",
                      provider=fake))
    assert fake.calls == [] and run.outcome.routed.path == "rules (offline)"
    assert fresh_settings.saved() == before


def test_an_llm_run_names_the_model(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")
    fake = FakeProvider([reply({"kind": "no_class_before", "time": "10:00", "strength": "soft"})])
    run = checked(cli(five_choices(tmp_path), "I'm not a morning person", provider=fake))
    assert "Parser: llm (gemini-3.5-flash-lite)" in run.out


# ---- what was understood, and what wasn't ----

def test_the_rules_used_are_listed_with_the_results(tmp_path):
    run = checked(cli(five_choices(tmp_path), "ideally no classes before 10, fridays off", "--offline"))
    assert "1. no class before 10:00 (soft, weight 5)" in run.out
    assert "2. Fri off (hard)" in run.out


def test_unparsed_fragments_are_printed_as_warnings(tmp_path):
    run = checked(cli(five_choices(tmp_path), "no classes after 18, I'm not a morning person", "--offline"))
    assert "Not understood:" in run.out and "I'm not a morning person" in run.out


def test_offline_unparsed_text_says_setup_would_let_it_through(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=none\n")
    run = cli(five_choices(tmp_path), "I'm not a morning person")
    assert "python -m scheduler setup" in run.out and "free-form" in run.out


def test_an_ambiguous_name_lists_the_candidates():
    # (a near-miss with one clear winner, like "Cohenn", is read as that name by the rule parser)
    run = cli(SAMPLE, "prefer Cohan", "--offline")
    assert '"prefer Cohan": could mean Cohen or Cohn; say which.' in run.out


@pytest.mark.parametrize("reason, candidates, says", [
    ("not_understood", [], 'Try rephrasing, e.g. "no classes before 10"'),
    ("unknown_name", ["Levi"], "did you mean Levi?"),
    ("unknown_name", [], "not a name in the catalog."),
    ("ambiguous_name", ["SHM", "SHO"], "could mean SHM or SHO"),
    ("invalid_time", [], "impossible time"),
    ("conflicting_strength", [], "keep one"),
])
def test_every_unparsed_reason_says_what_to_do(reason, candidates, says):
    fragment = UnparsedFragment(text="some words", reason=reason, candidates=candidates)
    assert unparsed_message(fragment).startswith('"some words": ') and says in unparsed_message(fragment)


def test_an_unknown_provider_setting_is_reported_and_the_run_continues_offline(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=gemeni\n")
    run = checked(cli(five_choices(tmp_path), "no classes before 10"))
    assert "gemeni" in run.out and run.outcome.routed.path == "rules (offline)" and run.code == 0


# ---- no solution, or out of time ----

def test_a_conflict_is_printed_with_exit_code_one():
    run = cli(SAMPLE, "fridays off", "--offline")
    assert run.code == 1
    assert "No schedule works: must take ZGC + Fri off conflict." in run.out
    assert headers(run.out) == []


def test_nothing_to_schedule_is_explained(tmp_path):
    run = cli(pool_catalog(tmp_path), "don't take R", "--offline")
    assert run.code == 1
    assert run.outcome.result.conflict and "No schedule works" in run.out


def test_hitting_the_time_limit_with_results_prints_them_and_the_note(tmp_path, monkeypatch):
    # start, first solve, second solve, then the third solve finds the time is up
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, 0, 0, LATE))
    run = checked(cli(five_choices(tmp_path), "--offline"))
    assert run.code == 0 and len(headers(run.out)) == 2
    assert "Stopped after 3 minutes: showing the 2 found; there may be more." in run.out


def test_hitting_the_time_limit_with_nothing_found_prints_the_note_with_exit_code_one(tmp_path, monkeypatch):
    monkeypatch.setattr(solver_module, "_clock", StepClock(0, LATE))
    run = cli(five_choices(tmp_path), "--offline")
    assert run.code == 1 and headers(run.out) == []
    assert "Stopped after 3 minutes" in run.out


# ---- what each schedule shows ----

def test_each_schedule_lists_every_session_with_day_time_room_and_instructor(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "--offline"))
    first = run.out.split("Schedule 1 of", 1)[1].split("Schedule 2 of", 1)[0]
    assert re.search(r"Sun 08:00-10:00\s+M lecture 1\s+room R1\s+Cohen", first)
    for s in run.outcome.result.schedules:
        assert len(s.choices) == 2  # lecture and lab


def test_a_merged_option_is_shown_as_one_or_the_other_with_both_rooms_and_instructors(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "--offline"))
    assert re.search(r"Mon 10:00-12:00\s+M lab 11 or 12\s+room L1 or L2\s+Adler or Brenner", run.out)


def test_later_schedules_say_how_they_differ_from_the_first(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "--offline"))
    first = {ch.component: ch.option.group_ids for ch in run.outcome.result.schedules[0].choices}["lab"]
    old, new = ("11 or 12", "21") if first == ("11", "12") else ("21", "11 or 12")
    assert f"Differs from #1: M lab {new} instead of {old}" in run.out


def test_which_member_to_register_for_is_shown_when_a_preference_picks_one(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "prefer Adler", "--offline"))
    best = run.outcome.result.schedules[0]
    assert {ch.component: ch.register_for for ch in best.choices}["lab"] == ["11"]
    assert "M lab 11 or 12" in run.out and "(register for 11)" in run.out


def test_a_tie_between_members_is_explained(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "prefer Adler, prefer Brenner", "--offline"))
    notes = [ch.register_note for s in run.outcome.result.schedules for ch in s.choices if ch.register_note]
    assert notes and all(n in run.out for n in notes)


def test_by_default_only_broken_rules_and_their_cost_are_shown(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "prefer Gal", "--offline"))
    assert "All rules met." in run.out.split("Schedule 2 of", 1)[0]  # #1 uses lab 21 (Gal)
    second = run.out.split("Schedule 2 of", 1)[1]
    assert re.search(r"Broken: prefer Gal \(soft, weight 5\): cost \d+", second)
    assert "must take M" not in run.out.split("Your rules", 1)[1]  # met hard rules aren't repeated


def test_verbose_lists_every_rule_met_or_broken(tmp_path):
    run = checked(cli(merged_catalog(tmp_path), "prefer Gal", "--offline", "--verbose"))
    first = run.out.split("Schedule 1 of", 1)[1].split("Schedule 2 of", 1)[0]
    assert re.search(r"met\s+must take M \(hard\)", first)
    assert re.search(r"met\s+prefer Gal \(soft, weight 5\)", first)


# ---- the preferences text ----

def test_no_preferences_text_solves_with_courses_alone(tmp_path):
    run = checked(cli(five_choices(tmp_path), "--offline"))
    assert run.outcome.routed is None and "Your rules: none" in run.out


def test_a_dash_reads_the_preferences_from_stdin(tmp_path):
    console = FakeConsole(stdin="no classes before 12\n")
    run = checked(cli(five_choices(tmp_path), "-", "--offline", console=console))
    assert [c.kind for c in run.outcome.constraints if c.kind != "must_take"] == ["no_class_before"]
    assert console.stdin_reads == 1


def test_a_dash_with_review_is_refused_since_both_need_stdin(tmp_path):
    run = cli(five_choices(tmp_path), "-", "--review", "--offline")
    assert run.code == 2 and "--review" in run.err


# ---- input errors: a readable message, never a traceback ----

def test_a_missing_catalog_file_is_reported(tmp_path):
    run = cli(tmp_path / "nope.json", "--offline")
    assert run.code == 2 and "nope.json" in run.err and "Traceback" not in run.err


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    run = cli(path, "--offline")
    assert run.code == 2 and "not valid JSON" in run.err


def test_a_catalog_schema_error_is_reported(tmp_path):
    path = write_catalog(tmp_path, catalog(course("A", lecture=[grp("1", "Mnd 8-10")]), raw=True))
    run = cli(path, "--offline")
    assert run.code == 2 and "Mnd" in run.err


def test_an_llm_pool_smaller_than_n_is_reported_before_solving(fresh_settings, tmp_path):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")
    fake = FakeProvider([reply({"kind": "take_n_of", "courses": ["P1", "P2"], "n": 3})])
    run = cli(pool_catalog(tmp_path), "I want three of the electives P1 or P2", provider=fake)
    assert run.code == 2 and "can't take 3 from a pool of 2" in run.err
    assert run.outcome.result is None


def test_no_arguments_prints_usage():
    run = cli()
    assert run.code == 2 and "usage" in run.err.lower()


def test_help_prints_usage_and_exits_cleanly():
    run = cli("--help")
    assert run.code == 0 and "--pick" in run.out and "--drop" in run.out
