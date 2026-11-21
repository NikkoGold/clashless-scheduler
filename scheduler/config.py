r"""Settings, and first-run setup writing them.

Where they're read from (see plan.md, Config):
1. a `.env` in the working directory, if there is one; it replaces the user-wide file entirely (not merged)
2. otherwise the user-wide file, `config.env` in user_dir(): %LOCALAPPDATA%\scheduler on Windows,
   $XDG_CONFIG_HOME/scheduler or ~/.config/scheduler elsewhere, or $SCHEDULER_HOME (the test suite sets it)
Real environment variables win over either file. No settings, or LLM_PROVIDER=none, means offline: the rule
parser alone. The parse cache and the daily call count live in the user-wide folder too, so one key used from
many folders still gets one daily limit.
"""
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from dotenv import dotenv_values

PROVIDERS = ("gemini", "anthropic", "openai", "ollama")
KEY_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
# OpenAI and Ollama have none: the cheapest OpenAI model changes, and Ollama depends on what's installed
# pinned: the SDK uses the v1 API, which has no "-latest" aliases
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "anthropic": "claude-haiku-5-5"}
DEFAULT_MAX_CALLS = 50
DEFAULT_OLLAMA_URL = "http://localhost:11434"
PROJECT_ENV = Path(".env")
HOME_VAR = "SCHEDULER_HOME"
SETTING_VARS = ("LLM_PROVIDER", "LLM_MODEL", *KEY_VARS.values(), "OLLAMA_URL", "LLM_MAX_CALLS_PER_DAY")


def user_dir(environ: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    """The user-wide folder for settings and the cache."""
    env = os.environ if environ is None else environ
    if env.get(HOME_VAR):
        return Path(env[HOME_VAR])
    if platform == "win32" and env.get("LOCALAPPDATA"):
        # LOCALAPPDATA, not APPDATA: APPDATA can be copied between machines on managed accounts
        return Path(env["LOCALAPPDATA"]) / "scheduler"
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "scheduler"
    return Path.home() / ".config" / "scheduler"


def user_config_path(environ: Mapping[str, str] | None = None) -> Path:
    return user_dir(environ) / "config.env"


def cache_dir(environ: Mapping[str, str] | None = None) -> Path:
    return user_dir(environ) / "cache"


@dataclass(frozen=True)
class Config:
    provider: str = "none"  # one of PROVIDERS, or "none" for offline
    model: str | None = None
    api_key: str | None = None
    ollama_url: str = DEFAULT_OLLAMA_URL
    max_calls_per_day: int = DEFAULT_MAX_CALLS
    needs_setup: bool = False  # nothing chosen yet: offer first-run setup
    problem: str | None = None  # a setting that couldn't be used, to show the user
    source: Path | None = None  # the settings file read, shown to the user (an override is otherwise invisible)
    from_environment: tuple[str, ...] = ()  # settings given by real environment variables, which win over the file


def load_config(env_path: Path | None = None, environ: Mapping[str, str] | None = None,
                project_env: Path | None = None) -> Config:
    """Read one settings file: `env_path` if given, else the project .env if it exists, else the user-wide file."""
    env = os.environ if environ is None else environ
    if env_path is None:
        project = PROJECT_ENV if project_env is None else Path(project_env)
        env_path = project if project.is_file() else user_config_path(env)
    is_file = Path(env_path).is_file()
    values = {k: v for k, v in dotenv_values(env_path).items() if v is not None} if is_file else {}
    from_environment = tuple(k for k in SETTING_VARS if k in env)
    values.update(env)
    problems = []

    raw = values.get("LLM_PROVIDER", "").strip()
    provider = raw.lower()
    if provider and provider != "none" and provider not in PROVIDERS:
        problems.append(f"LLM_PROVIDER={raw!r} isn't a provider (use one of {', '.join(PROVIDERS)}, or none); running offline")
        provider = "none"

    max_calls = DEFAULT_MAX_CALLS
    if values.get("LLM_MAX_CALLS_PER_DAY", "").strip():
        try:
            max_calls = int(values["LLM_MAX_CALLS_PER_DAY"])
            if max_calls < 0:
                raise ValueError
        except ValueError:
            problems.append(f"LLM_MAX_CALLS_PER_DAY={values['LLM_MAX_CALLS_PER_DAY']!r} isn't a whole number; using {DEFAULT_MAX_CALLS}")
            max_calls = DEFAULT_MAX_CALLS

    return Config(
        provider=provider or "none",
        model=(values.get("LLM_MODEL") or "").strip() or DEFAULT_MODELS.get(provider),
        api_key=(values.get(KEY_VARS[provider]) or "").strip() or None if provider in KEY_VARS else None,
        ollama_url=(values.get("OLLAMA_URL") or "").strip() or DEFAULT_OLLAMA_URL,
        max_calls_per_day=max_calls,
        needs_setup=not raw,
        problem="; ".join(problems) or None,
        source=Path(env_path).resolve() if is_file else None,
        from_environment=from_environment,
    )


@dataclass(frozen=True)
class SetupReport:
    path: Path
    message: str
    warning: str | None = None


def git_ignored(path: Path) -> bool | None:
    """Whether git would leave this file out of commits; None if it isn't in a git repository (or git is missing)."""
    path = Path(path).resolve()
    try:
        done = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", path.name],
                              capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(done.returncode)  # 128: not a repository


def write_setup(env_path: Path | None = None, provider: str = "none", model: str | None = None,
                api_key: str | None = None, ollama_url: str | None = None,
                is_ignored: Callable[[Path], bool | None] = git_ignored,
                project_env: Path = PROJECT_ENV) -> SetupReport:
    """Save the first-run choice: a provider (with its model and key), or "none" to stay offline.

    Writes the user-wide file unless `env_path` is given. Lines already in the file are kept, and the settings
    written here replace their old values; with no `api_key`, a key already saved is kept.
    """
    provider = provider.strip().lower()
    if provider != "none" and provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}: use one of {', '.join(PROVIDERS)}, or none")
    settings = {"LLM_PROVIDER": provider}
    if provider != "none":
        model = model or DEFAULT_MODELS.get(provider)
        if not model:
            raise ValueError(f"{provider} has no default model: give the model name to use")
        settings["LLM_MODEL"] = model
        if provider in KEY_VARS and api_key:
            settings[KEY_VARS[provider]] = api_key
        if provider == "ollama" and ollama_url:
            settings["OLLAMA_URL"] = ollama_url

    path = Path(user_config_path() if env_path is None else env_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in settings]
    path.write_text("\n".join(kept + [f"{k}={v}" for k, v in settings.items()]) + "\n", encoding="utf-8")

    message = f"Saved to {path}"
    if provider == "none":
        message += ". Offline: only simple, direct phrasing is understood; run setup again to add an LLM."
    warnings = []
    if provider in KEY_VARS and api_key and is_ignored(path) is False:
        warnings.append(f"{path.name} isn't git-ignored, and it holds your API key in plain text: add it to .gitignore")
    project = Path(project_env).resolve()
    if project != path and project.is_file():
        warnings.append(f"A .env in this folder ({project}) overrides these settings when you run from here.")
    return SetupReport(path=path, message=message, warning=" ".join(warnings) or None)
