"""First run and `python -m scheduler setup`: where the choice is saved, what's asked, and that keys stay hidden.

Each test has its own empty user-wide folder and working folder (fresh_settings): a true first run.
"""
import pytest

from tests.helpers.catalog_builder import catalog, course, grp
from tests.helpers.cli_runner import cli, write_catalog
from tests.helpers.fake_console import FakeConsole
from tests.helpers.fake_provider import FakeProvider, reply

VAGUE = "I'm not a morning person"
LLM_REPLY = reply({"kind": "no_class_before", "time": "10:00", "strength": "soft"})
NOTICE = "Setup is required to understand anything beyond basic phrases"


@pytest.fixture
def cat(tmp_path):
    return write_catalog(tmp_path, catalog(course("A", required=True, lecture=[grp("1", "Sun 10-12")]), raw=True))


# ---- first run ----

def test_first_run_without_a_terminal_prints_a_notice_and_never_waits(fresh_settings, cat):
    console = FakeConsole(tty=False)
    run = cli(cat, "no classes before 10", console=console)
    assert NOTICE in run.out and "python -m scheduler setup" in run.out
    assert console.prompts == []
    assert run.code == 0 and run.outcome.result.schedules
    assert fresh_settings.saved() == ""  # nothing chosen, so it's offered again next time


def test_first_run_in_a_terminal_asks_and_offline_is_saved_for_good(fresh_settings, cat):
    run = cli(cat, "no classes before 10", console=FakeConsole(["o"], tty=True))
    assert "LLM_PROVIDER=none" in fresh_settings.saved()
    assert run.code == 0 and run.outcome.routed.path == "rules (offline)"
    again = FakeConsole(tty=True)
    cli(cat, "no classes before 10", console=again)
    assert again.prompts == []


def test_first_run_later_runs_offline_saves_nothing_and_asks_next_time(fresh_settings, cat):
    run = cli(cat, "no classes before 10", console=FakeConsole(["l"], tty=True))
    assert run.code == 0 and run.outcome.routed.path == "rules (offline)"
    assert fresh_settings.saved() == ""
    again = FakeConsole(["l"], tty=True)
    cli(cat, "no classes before 10", console=again)
    assert len(again.prompts) == 1


def test_first_run_end_of_input_at_the_question_counts_as_later(fresh_settings, cat):
    run = cli(cat, "no classes before 10", console=FakeConsole([], tty=True))
    assert run.code == 0 and fresh_settings.saved() == ""


def test_first_run_an_unclear_answer_asks_again(fresh_settings, cat):
    console = FakeConsole(["maybe", "o"], tty=True)
    cli(cat, "no classes before 10", console=console)
    assert len(console.prompts) == 2 and "LLM_PROVIDER=none" in fresh_settings.saved()


def test_first_run_set_up_now_runs_setup_then_this_run_uses_the_new_settings(fresh_settings, cat):
    console = FakeConsole(["s", "1", ""], secrets=["the-key"], tty=True)
    fake = FakeProvider([LLM_REPLY])
    run = cli(cat, VAGUE, console=console, provider=fake)
    assert "GEMINI_API_KEY=the-key" in fresh_settings.saved()
    assert run.outcome.routed.path == "llm" and len(fake.calls) == 1


def test_first_run_with_offline_flag_doesnt_ask(fresh_settings, cat):
    console = FakeConsole(tty=True)
    run = cli(cat, "no classes before 10", "--offline", console=console)
    assert console.prompts == [] and run.code == 0


def test_first_run_with_preferences_from_stdin_doesnt_ask(fresh_settings, cat):
    # stdin is the preferences, not answers
    console = FakeConsole(tty=True, stdin="no classes before 10")
    run = cli(cat, "-", console=console)
    assert console.prompts == [] and NOTICE in run.out


# ---- once offline is chosen ----

def test_after_choosing_offline_there_is_no_prompt_or_notice_only_the_way_back(fresh_settings, cat):
    fresh_settings.save("LLM_PROVIDER=none\n")
    console = FakeConsole(tty=True)
    run = cli(cat, "no classes before 10", console=console)
    assert console.prompts == [] and NOTICE not in run.out
    assert 'Parser: rules (offline): run "python -m scheduler setup" to add an LLM' in run.out


def test_setup_after_choosing_offline_switches_to_the_llm(fresh_settings, cat):
    fresh_settings.save("LLM_PROVIDER=none\n")
    run = cli("setup", console=FakeConsole(["gemini", ""], secrets=["k2"]))
    assert run.code == 0
    fake = FakeProvider([LLM_REPLY])
    assert cli(cat, VAGUE, provider=fake).outcome.routed.path == "llm"


# ---- the setup command ----

@pytest.mark.parametrize("choice", ["", "1", "gemini", "Gemini"])
def test_setup_gemini_with_the_default_model(fresh_settings, choice):
    console = FakeConsole([choice, ""], secrets=["g-key"])
    run = cli("setup", console=console)
    saved = fresh_settings.saved()
    assert run.code == 0
    assert "LLM_PROVIDER=gemini" in saved and "LLM_MODEL=gemini-3.5-flash-lite" in saved and "GEMINI_API_KEY=g-key" in saved


def test_setup_anthropic_with_a_chosen_model(fresh_settings):
    run = cli("setup", console=FakeConsole(["2", "claude-other"], secrets=["a-key"]))
    saved = fresh_settings.saved()
    assert run.code == 0
    assert "LLM_PROVIDER=anthropic" in saved and "LLM_MODEL=claude-other" in saved and "ANTHROPIC_API_KEY=a-key" in saved


def test_the_key_is_read_hidden_and_never_printed(fresh_settings):
    run = cli("setup", console=FakeConsole(["1", ""], secrets=["super-secret-key"]))
    assert run.console.secret_prompts and "super-secret-key" not in run.out + run.err
    assert all("super-secret-key" not in p for p in run.console.prompts)


@pytest.mark.parametrize("choice, provider", [("3", "openai"), ("4", "ollama")])
def test_providers_without_a_default_model_ask_until_one_is_given(fresh_settings, choice, provider):
    answers = [choice, "", "my-model"] + ([""] if provider == "ollama" else [])
    secrets = ["o-key"] if provider == "openai" else []
    run = cli("setup", console=FakeConsole(answers, secrets=secrets))
    assert run.code == 0
    assert f"LLM_PROVIDER={provider}" in fresh_settings.saved() and "LLM_MODEL=my-model" in fresh_settings.saved()


def test_ollama_asks_for_no_key_and_offers_the_default_url(fresh_settings):
    console = FakeConsole(["ollama", "llama3", "http://box:11434"])
    run = cli("setup", console=console)
    assert run.code == 0 and console.secret_prompts == []
    assert "OLLAMA_URL=http://box:11434" in fresh_settings.saved()


def test_an_empty_key_with_none_saved_is_asked_again(fresh_settings):
    console = FakeConsole(["1", ""], secrets=["", "k"])
    cli("setup", console=console)
    assert len(console.secret_prompts) == 2 and "GEMINI_API_KEY=k" in fresh_settings.saved()


def test_rerunning_setup_enter_keeps_the_saved_key(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=old-key\n")
    console = FakeConsole(["1", "gemini-other"], secrets=[""])
    cli("setup", console=console)
    assert "Enter keeps the saved one" in console.secret_prompts[0]
    saved = fresh_settings.saved()
    assert "GEMINI_API_KEY=old-key" in saved and "LLM_MODEL=gemini-other" in saved


def test_rerunning_setup_with_another_provider_replaces_the_choice(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=g\n")
    cli("setup", console=FakeConsole(["anthropic", ""], secrets=["a"]))
    saved = fresh_settings.saved()
    assert "LLM_PROVIDER=anthropic" in saved and saved.count("LLM_PROVIDER=") == 1


def test_setup_can_choose_offline(fresh_settings):
    run = cli("setup", console=FakeConsole(["5"]))
    assert run.code == 0 and "LLM_PROVIDER=none" in fresh_settings.saved()


def test_an_unknown_provider_choice_asks_again(fresh_settings):
    console = FakeConsole(["9", "chatgpt", "5"])
    cli("setup", console=console)
    assert len(console.prompts) == 3 and "LLM_PROVIDER=none" in fresh_settings.saved()


@pytest.mark.parametrize("answers, secrets", [
    ([KeyboardInterrupt()], []),   # Ctrl+C at the provider question
    (["1", ""], []),               # end of input at the key
    (["3"], []),                   # end of input at the model name
])
def test_interrupted_setup_writes_nothing(fresh_settings, answers, secrets):
    run = cli("setup", console=FakeConsole(answers, secrets=secrets))
    assert run.code == 1 and fresh_settings.saved() == ""
    assert "nothing was saved" in run.out.lower()


def test_setup_prints_the_full_path_it_wrote_to(fresh_settings):
    run = cli("setup", console=FakeConsole(["5"]))
    assert str(fresh_settings.user_file.resolve()) in run.out


def test_setup_warns_when_a_project_env_here_overrides_it(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=none\n", project=True)
    run = cli("setup", console=FakeConsole(["1", ""], secrets=["k"]))
    assert "overrides" in run.out and str(fresh_settings.project_env.resolve()) in run.out
