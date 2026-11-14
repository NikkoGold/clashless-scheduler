"""config.py: reading .env (with defaults) and writing it from first-run setup. Never touches the real .env."""
import shutil
import subprocess
from pathlib import Path

import pytest

from scheduler.config import DEFAULT_MAX_CALLS, DEFAULT_OLLAMA_URL, git_ignored, load_config, write_setup

ROOT = Path(__file__).parent.parent


def env_file(tmp_path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# ---- reading ----

def test_no_env_file_means_offline_and_setup_is_offered(tmp_path):
    cfg = load_config(tmp_path / ".env", environ={})
    assert cfg.provider == "none"
    assert cfg.needs_setup


def test_saved_offline_choice_means_offline_without_offering_setup_again(tmp_path):
    cfg = load_config(env_file(tmp_path, "LLM_PROVIDER=none\n"), environ={})
    assert cfg.provider == "none"
    assert not cfg.needs_setup


def test_values_are_read_from_the_env_file(tmp_path):
    path = env_file(tmp_path, "LLM_PROVIDER=gemini\nLLM_MODEL=gemini-x\nGEMINI_API_KEY=k123\nLLM_MAX_CALLS_PER_DAY=7\n")
    cfg = load_config(path, environ={})
    assert (cfg.provider, cfg.model, cfg.api_key, cfg.max_calls_per_day) == ("gemini", "gemini-x", "k123", 7)
    assert not cfg.needs_setup and cfg.problem is None


@pytest.mark.parametrize("provider, model", [("gemini", "gemini-flash-lite-latest"), ("anthropic", "claude-haiku-5-5")])
def test_a_provider_without_a_model_gets_its_default_model(tmp_path, provider, model):
    cfg = load_config(env_file(tmp_path, f"LLM_PROVIDER={provider}\n"), environ={})
    assert cfg.model == model


def test_the_call_limit_and_ollama_url_have_defaults(tmp_path):
    cfg = load_config(env_file(tmp_path, "LLM_PROVIDER=ollama\nLLM_MODEL=llama3\n"), environ={})
    assert cfg.max_calls_per_day == DEFAULT_MAX_CALLS == 50
    assert cfg.ollama_url == DEFAULT_OLLAMA_URL == "http://localhost:11434"


def test_only_the_selected_providers_key_is_used(tmp_path):
    path = env_file(tmp_path, "LLM_PROVIDER=anthropic\nGEMINI_API_KEY=g\nANTHROPIC_API_KEY=a\n")
    assert load_config(path, environ={}).api_key == "a"


def test_real_environment_variables_win_over_the_env_file(tmp_path):
    path = env_file(tmp_path, "LLM_PROVIDER=gemini\nGEMINI_API_KEY=from-file\n")
    assert load_config(path, environ={"GEMINI_API_KEY": "from-env"}).api_key == "from-env"


def test_reads_the_process_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert not load_config(tmp_path / ".env").needs_setup


@pytest.mark.parametrize("value", ["gemeni", "chatgpt", "gemini-flash-lite-latest"])  # a typo, a product, a model
def test_an_unknown_provider_runs_offline_with_a_clear_message(tmp_path, value):
    cfg = load_config(env_file(tmp_path, f"LLM_PROVIDER={value}\n"), environ={})
    assert cfg.provider == "none"
    assert value.strip() in cfg.problem and "gemini" in cfg.problem  # names what was given and what's allowed


def test_provider_names_are_case_insensitive(tmp_path):
    assert load_config(env_file(tmp_path, "LLM_PROVIDER=Gemini\n"), environ={}).provider == "gemini"


@pytest.mark.parametrize("value", ["lots", "-5", "2.5"])
def test_a_bad_call_limit_uses_the_default_with_a_message(tmp_path, value):
    cfg = load_config(env_file(tmp_path, f"LLM_PROVIDER=gemini\nLLM_MAX_CALLS_PER_DAY={value}\n"), environ={})
    assert cfg.max_calls_per_day == DEFAULT_MAX_CALLS
    assert "LLM_MAX_CALLS_PER_DAY" in cfg.problem


def test_llm_enabled_is_gone(tmp_path):
    # LLM_PROVIDER=none is the only offline switch
    path = env_file(tmp_path, "LLM_ENABLED=false\nLLM_PROVIDER=gemini\nGEMINI_API_KEY=k\n")
    assert load_config(path, environ={}).provider == "gemini"
    assert "LLM_ENABLED" not in (ROOT / ".env.example").read_text(encoding="utf-8")


# ---- writing (first-run setup) ----

def test_setup_writes_provider_model_and_key(tmp_path):
    path = tmp_path / ".env"
    write_setup(path, "gemini", api_key="k1", is_ignored=lambda p: True)
    cfg = load_config(path, environ={})
    assert (cfg.provider, cfg.model, cfg.api_key) == ("gemini", "gemini-flash-lite-latest", "k1")


def test_continue_offline_writes_provider_none(tmp_path):
    path = tmp_path / ".env"
    write_setup(path, "none", is_ignored=lambda p: True)
    assert "LLM_PROVIDER=none" in path.read_text(encoding="utf-8")
    assert not load_config(path, environ={}).needs_setup


def test_setup_keeps_unrelated_lines_and_replaces_old_values(tmp_path):
    path = env_file(tmp_path, "# my notes\nLLM_PROVIDER=none\nOLLAMA_URL=http://box:1\nGEMINI_API_KEY=old\n")
    write_setup(path, "gemini", api_key="new", is_ignored=lambda p: True)
    text = path.read_text(encoding="utf-8")
    assert "# my notes" in text and "OLLAMA_URL=http://box:1" in text
    assert text.count("LLM_PROVIDER=") == 1 and text.count("GEMINI_API_KEY=") == 1
    assert load_config(path, environ={}).api_key == "new"


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_setup_needs_a_model_name_for_providers_without_a_default(tmp_path, provider):
    with pytest.raises(ValueError, match="model"):
        write_setup(tmp_path / ".env", provider, api_key="k", is_ignored=lambda p: True)


def test_setup_for_ollama_writes_the_model_and_no_key(tmp_path):
    path = tmp_path / ".env"
    report = write_setup(path, "ollama", model="llama3", is_ignored=lambda p: False)
    assert load_config(path, environ={}).model == "llama3"
    assert "KEY" not in path.read_text(encoding="utf-8")
    assert report.warning is None  # nothing secret was written


def test_setup_rejects_an_unknown_provider(tmp_path):
    with pytest.raises(ValueError, match="gemeni"):
        write_setup(tmp_path / ".env", "gemeni", api_key="k", is_ignored=lambda p: True)


def test_setup_reports_the_full_path_it_wrote_to(tmp_path):
    report = write_setup(tmp_path / ".env", "none", is_ignored=lambda p: True)
    assert report.path == (tmp_path / ".env").resolve()
    assert str(report.path) in report.message


def test_setup_warns_when_env_is_not_git_ignored(tmp_path):
    report = write_setup(tmp_path / ".env", "gemini", api_key="k", is_ignored=lambda p: False)
    assert report.warning and "git" in report.warning


@pytest.mark.parametrize("ignored", [True, None])  # None: not in a git repository
def test_setup_doesnt_warn_when_env_is_ignored_or_not_in_a_repo(tmp_path, ignored):
    assert write_setup(tmp_path / ".env", "gemini", api_key="k", is_ignored=lambda p: ignored).warning is None


# ---- the git-ignore check itself ----

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _repo(tmp_path, gitignore: str | None) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if gitignore is not None:
        (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    return tmp_path / ".env"


@needs_git
def test_git_ignored_is_true_for_an_ignored_env(tmp_path):
    assert git_ignored(_repo(tmp_path, ".env\n")) is True


@needs_git
def test_git_ignored_is_false_for_an_env_that_would_be_committed(tmp_path):
    assert git_ignored(_repo(tmp_path, None)) is False


@needs_git
def test_git_ignored_is_none_outside_a_repo(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert git_ignored(outside / ".env") is None


def test_git_ignored_is_none_when_git_cant_run(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert git_ignored(tmp_path / ".env") is None


def test_the_projects_own_env_is_git_ignored():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()
