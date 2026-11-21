"""config.py: reading settings (with defaults) and writing them from first-run setup.

Never touches the real settings: conftest points the user-wide folder and the working directory at temporary ones.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from scheduler.config import (
    DEFAULT_MAX_CALLS,
    DEFAULT_OLLAMA_URL,
    cache_dir,
    git_ignored,
    load_config,
    user_config_path,
    user_dir,
    write_setup,
)

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


@pytest.mark.parametrize("provider, model", [("gemini", "gemini-3.5-flash-lite"), ("anthropic", "claude-haiku-5-5")])
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


@pytest.mark.parametrize("value", ["gemeni", "chatgpt", "gemini-3.5-flash-lite"])  # a typo, a product, a model
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
    assert (cfg.provider, cfg.model, cfg.api_key) == ("gemini", "gemini-3.5-flash-lite", "k1")


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


# ---- where settings live: a user-wide file, overridden by a project .env ----

def test_the_user_wide_folder_is_in_local_app_data_on_windows():
    env = {"LOCALAPPDATA": "C:/Users/u/AppData/Local", "XDG_CONFIG_HOME": "/x"}
    assert user_dir(env, platform="win32") == Path("C:/Users/u/AppData/Local") / "scheduler"


def test_the_user_wide_folder_follows_xdg_elsewhere():
    assert user_dir({"XDG_CONFIG_HOME": "/home/u/.cfg"}, platform="linux") == Path("/home/u/.cfg") / "scheduler"


@pytest.mark.parametrize("platform", ["linux", "win32"])  # Windows without LOCALAPPDATA too
def test_the_user_wide_folder_falls_back_to_dot_config(platform):
    assert user_dir({}, platform=platform) == Path.home() / ".config" / "scheduler"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_scheduler_home_overrides_the_user_wide_folder(tmp_path, platform):
    env = {"SCHEDULER_HOME": str(tmp_path), "LOCALAPPDATA": "C:/x", "XDG_CONFIG_HOME": "/x"}
    assert user_dir(env, platform=platform) == tmp_path


def test_the_settings_file_and_cache_live_in_the_user_wide_folder(tmp_path):
    env = {"SCHEDULER_HOME": str(tmp_path)}
    assert user_config_path(env) == tmp_path / "config.env"
    assert cache_dir(env) == tmp_path / "cache"


@pytest.fixture
def places(tmp_path):
    """Settings pointing at an empty user-wide folder, its settings file, and a project .env (not yet written)."""
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    return {"SCHEDULER_HOME": str(home)}, home / "config.env", work / ".env"


def test_with_no_project_env_the_user_wide_file_is_used(places):
    env, user_file, project = places
    user_file.write_text("LLM_PROVIDER=gemini\nGEMINI_API_KEY=user\n", encoding="utf-8")
    cfg = load_config(environ=env, project_env=project)
    assert (cfg.provider, cfg.api_key) == ("gemini", "user")
    assert cfg.source == user_file.resolve()


def test_a_project_env_replaces_the_user_wide_file_entirely(places):
    # not merged: the user file's key must not be paired with the project's provider
    env, user_file, project = places
    user_file.write_text("LLM_PROVIDER=gemini\nGEMINI_API_KEY=user\nLLM_MAX_CALLS_PER_DAY=9\n", encoding="utf-8")
    project.write_text("LLM_PROVIDER=gemini\n", encoding="utf-8")
    cfg = load_config(environ=env, project_env=project)
    assert cfg.api_key is None and cfg.max_calls_per_day == DEFAULT_MAX_CALLS
    assert cfg.source == project.resolve()


def test_environment_variables_override_both_files_and_are_named(places):
    env, user_file, project = places
    project.write_text("LLM_PROVIDER=gemini\nGEMINI_API_KEY=project\n", encoding="utf-8")
    cfg = load_config(environ={**env, "GEMINI_API_KEY": "from-env"}, project_env=project)
    assert cfg.api_key == "from-env"
    assert cfg.from_environment == ("GEMINI_API_KEY",)


def test_with_no_settings_anywhere_the_source_is_none_and_setup_is_offered(places):
    env, _, project = places
    cfg = load_config(environ=env, project_env=project)
    assert cfg.source is None and cfg.from_environment == () and cfg.needs_setup


def test_settings_given_only_by_the_environment_need_no_setup(places):
    env, _, project = places
    cfg = load_config(environ={**env, "LLM_PROVIDER": "none"}, project_env=project)
    assert cfg.source is None and cfg.from_environment == ("LLM_PROVIDER",) and not cfg.needs_setup


@pytest.mark.parametrize("where", ["user", "project"])
def test_a_saved_choice_in_either_file_means_setup_isnt_offered(places, where):
    env, user_file, project = places
    (user_file if where == "user" else project).write_text("LLM_PROVIDER=none\n", encoding="utf-8")
    assert not load_config(environ=env, project_env=project).needs_setup


def test_setup_writes_the_user_wide_file_by_default_creating_its_folder(tmp_path, monkeypatch):
    home = tmp_path / "new" / "home"
    monkeypatch.setenv("SCHEDULER_HOME", str(home))
    report = write_setup(provider="gemini", api_key="k", is_ignored=lambda p: None)
    assert report.path == (home / "config.env").resolve()
    assert load_config(environ={"SCHEDULER_HOME": str(home)}, project_env=tmp_path / "none.env").api_key == "k"


def test_setup_without_a_key_keeps_the_saved_key(tmp_path):
    path = env_file(tmp_path, "LLM_PROVIDER=gemini\nGEMINI_API_KEY=saved\n")
    write_setup(path, "gemini", model="gemini-other", is_ignored=lambda p: True)
    cfg = load_config(path, environ={})
    assert (cfg.model, cfg.api_key) == ("gemini-other", "saved")


def test_setup_writes_an_ollama_url_when_given(tmp_path):
    path = tmp_path / "config.env"
    write_setup(path, "ollama", model="llama3", ollama_url="http://box:11434", is_ignored=lambda p: None)
    assert load_config(path, environ={}).ollama_url == "http://box:11434"


def test_setup_warns_when_a_project_env_here_will_override_it(places):
    env, user_file, project = places
    project.write_text("LLM_PROVIDER=none\n", encoding="utf-8")
    report = write_setup(user_file, "gemini", api_key="k", is_ignored=lambda p: None, project_env=project)
    assert report.warning and str(project.resolve()) in report.warning and "overrides" in report.warning


def test_setup_into_the_project_env_itself_doesnt_warn_about_overriding(places):
    _, _, project = places
    report = write_setup(project, "none", is_ignored=lambda p: True, project_env=project)
    assert report.warning is None


# ---- the test suite itself never touches the real settings ----

def test_the_suite_points_the_user_wide_folder_at_a_temporary_one(tmp_path_factory):
    base = tmp_path_factory.getbasetemp().resolve()
    assert base in user_dir().resolve().parents


def test_the_suite_runs_outside_the_project_so_its_env_is_never_read():
    assert Path.cwd().resolve() != ROOT.resolve()
    assert load_config().source is None
