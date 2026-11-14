"""Settings read from .env, and first-run setup writing it.

No .env, or LLM_PROVIDER=none, means offline: the rule parser alone. Real environment variables win over the file.
"""
import os
import subprocess
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
ENV_PATH = Path(".env")


@dataclass(frozen=True)
class Config:
    provider: str = "none"  # one of PROVIDERS, or "none" for offline
    model: str | None = None
    api_key: str | None = None
    ollama_url: str = DEFAULT_OLLAMA_URL
    max_calls_per_day: int = DEFAULT_MAX_CALLS
    needs_setup: bool = False  # nothing chosen yet: offer first-run setup
    problem: str | None = None  # a setting that couldn't be used, to show the user


def load_config(env_path: Path = ENV_PATH, environ: Mapping[str, str] | None = None) -> Config:
    values = {k: v for k, v in dotenv_values(env_path).items() if v is not None} if Path(env_path).is_file() else {}
    values.update(os.environ if environ is None else environ)
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


def write_setup(env_path: Path = ENV_PATH, provider: str = "none", model: str | None = None, api_key: str | None = None,
                is_ignored: Callable[[Path], bool | None] = git_ignored) -> SetupReport:
    """Save the first-run choice: a provider (with its model and key), or "none" to stay offline.

    Lines already in the file are kept; the settings written here replace their old values.
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

    path = Path(env_path).resolve()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in settings]
    path.write_text("\n".join(kept + [f"{k}={v}" for k, v in settings.items()]) + "\n", encoding="utf-8")

    message = f"Saved to {path}"
    if provider == "none":
        message += ". Offline: only simple, direct phrasing is understood; run setup again to add an LLM."
    warning = None
    if provider in KEY_VARS and api_key and is_ignored(path) is False:
        warning = f"{path.name} isn't git-ignored, and it holds your API key in plain text: add it to .gitignore"
    return SetupReport(path=path, message=message, warning=warning)
