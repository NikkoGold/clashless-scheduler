"""LLM provider adapters. Each SDK is optional and only imported when its provider is selected.

An adapter sends the fixed instructions, the user message and the output schema, and returns the reply's JSON text.
"""
from typing import Protocol

from scheduler.config import KEY_VARS, Config

MAX_OUTPUT_TOKENS = 1024  # a reply is a few short constraints; this caps a runaway answer


class ProviderUnavailable(Exception):
    """The selected provider can't be used (no key, SDK not installed, offline config). The message says why."""


class Provider(Protocol):
    name: str
    model: str
    counts_toward_budget: bool  # False for local models, which cost nothing
    model_version: str | None  # the model that actually answered, once a call has been made

    def complete(self, system: str, user: str, schema: dict) -> str: ...


def import_sdk(provider: str, module: str):
    """Import an optional SDK, or explain how to install it."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError:
        raise ProviderUnavailable(f'the {provider} provider needs its SDK: pip install -e ".[{provider}]"') from None


def make_provider(config: Config) -> Provider:
    if config.provider in KEY_VARS and not config.api_key:
        raise ProviderUnavailable(f"no API key for {config.provider} (set {KEY_VARS[config.provider]} or run setup)")
    if config.provider == "gemini":
        from scheduler.parsing.providers.gemini import GeminiProvider
        return GeminiProvider(api_key=config.api_key, model=config.model)
    if config.provider == "anthropic":
        from scheduler.parsing.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=config.api_key, model=config.model)
    if config.provider == "openai":
        from scheduler.parsing.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key=config.api_key, model=config.model)
    if config.provider == "ollama":
        from scheduler.parsing.providers.ollama import OllamaProvider
        return OllamaProvider(url=config.ollama_url, model=config.model)
    raise ProviderUnavailable("no LLM is set up (offline)")
