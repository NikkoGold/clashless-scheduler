"""Provider adapters and make_provider: SDK clients are mocked (never HTTP to a real service), and the SDKs are optional."""
import importlib
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from scheduler.config import Config
from scheduler.parsing.providers import MAX_OUTPUT_TOKENS, ProviderUnavailable, make_provider
from scheduler.parsing.providers.anthropic import AnthropicProvider
from scheduler.parsing.providers.gemini import GeminiProvider
from scheduler.parsing.providers.ollama import OllamaProvider
from scheduler.parsing.providers.openai import OpenAIProvider

SCHEMA = {"type": "object", "properties": {"constraints": {"type": "array"}}}
PAYLOAD = {"constraints": [{"kind": "day_off", "day": "Fri"}], "unparsed": []}
SDK_MODULES = {"gemini": "google.genai", "anthropic": "anthropic", "openai": "openai"}


def config(provider, model="m", api_key="k", **kw) -> Config:
    return Config(provider=provider, model=model, api_key=api_key, **kw)


class Recorder:
    """A fake SDK method: records its keyword arguments and returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.response


# ---- each adapter, with a mocked SDK client ----

def gemini_client(text):
    call = Recorder(SimpleNamespace(text=text, model_version="gemini-flash-lite-007"))
    return SimpleNamespace(models=SimpleNamespace(generate_content=call)), call


def anthropic_client(payload):
    blocks = [SimpleNamespace(type="text", text="Here you go"), SimpleNamespace(type="tool_use", name="record_constraints", input=payload)]
    call = Recorder(SimpleNamespace(content=blocks, model="claude-haiku-5-5-20261001"))
    return SimpleNamespace(messages=SimpleNamespace(create=call)), call


def openai_client(text):
    call = Recorder(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], model="gpt-x-0601"))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call))), call


def test_gemini_returns_the_json_text_and_sends_the_cheap_settings():
    client, call = gemini_client(json.dumps(PAYLOAD))
    p = GeminiProvider(api_key="k", model="gemini-flash-lite-latest", client=client)
    assert json.loads(p.complete("SYS", "USER", SCHEMA)) == PAYLOAD
    cfg = call.kwargs["config"]
    assert call.kwargs["model"] == "gemini-flash-lite-latest" and call.kwargs["contents"] == "USER"
    assert cfg["system_instruction"] == "SYS"
    assert cfg["response_mime_type"] == "application/json" and cfg["response_json_schema"] == SCHEMA
    assert cfg["temperature"] == 0 and cfg["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert cfg["thinking_config"] == {"thinking_budget": 0}


def test_anthropic_returns_the_forced_tool_call_input_and_sends_the_cheap_settings():
    client, call = anthropic_client(PAYLOAD)
    p = AnthropicProvider(api_key="k", model="claude-haiku-5-5", client=client)
    assert json.loads(p.complete("SYS", "USER", SCHEMA)) == PAYLOAD
    kw = call.kwargs
    assert kw["system"] == "SYS" and kw["messages"] == [{"role": "user", "content": "USER"}]
    assert kw["tools"][0]["input_schema"] == SCHEMA
    assert kw["tool_choice"] == {"type": "tool", "name": kw["tools"][0]["name"]}
    assert kw["temperature"] == 0 and kw["max_tokens"] == MAX_OUTPUT_TOKENS


def test_anthropic_without_a_tool_call_raises():
    client, call = anthropic_client(PAYLOAD)
    call.response.content = [SimpleNamespace(type="text", text="no")]
    with pytest.raises(ValueError, match="tool"):
        AnthropicProvider(api_key="k", model="m", client=client).complete("S", "U", SCHEMA)


def test_openai_returns_the_json_text_and_sends_the_cheap_settings():
    client, call = openai_client(json.dumps(PAYLOAD))
    p = OpenAIProvider(api_key="k", model="gpt-x", client=client)
    assert json.loads(p.complete("SYS", "USER", SCHEMA)) == PAYLOAD
    kw = call.kwargs
    assert kw["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert kw["response_format"]["type"] == "json_schema" and kw["response_format"]["json_schema"]["schema"] == SCHEMA
    assert kw["temperature"] == 0 and kw["max_completion_tokens"] == MAX_OUTPUT_TOKENS


def test_ollama_posts_to_the_chat_api_and_returns_the_json_text():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "llama3", "message": {"role": "assistant", "content": json.dumps(PAYLOAD)}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = OllamaProvider(url="http://box:11434", model="llama3", client=client)
    assert json.loads(p.complete("SYS", "USER", SCHEMA)) == PAYLOAD
    body = seen["body"]
    assert seen["url"] == "http://box:11434/api/chat"
    assert body["format"] == SCHEMA and body["stream"] is False
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert body["options"] == {"temperature": 0, "num_predict": MAX_OUTPUT_TOKENS}


def test_ollama_http_errors_raise():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(httpx.HTTPStatusError):
        OllamaProvider(url="http://box:11434", model="nope", client=client).complete("S", "U", SCHEMA)


@pytest.mark.parametrize(
    "make, version",
    [
        (lambda: GeminiProvider(api_key="k", model="m", client=gemini_client("{}")[0]), "gemini-flash-lite-007"),
        (lambda: AnthropicProvider(api_key="k", model="m", client=anthropic_client({})[0]), "claude-haiku-5-5-20261001"),
        (lambda: OpenAIProvider(api_key="k", model="m", client=openai_client("{}")[0]), "gpt-x-0601"),
    ],
    ids=["gemini", "anthropic", "openai"],
)
def test_adapters_record_the_model_version_that_answered(make, version):
    # the live eval prints it, because "-latest" aliases change underneath us
    p = make()
    p.complete("S", "U", SCHEMA)
    assert p.model_version == version


def test_only_ollama_is_exempt_from_the_daily_limit():
    assert OllamaProvider(url="http://x", model="m", client=httpx.Client()).counts_toward_budget is False
    assert GeminiProvider(api_key="k", model="m", client=gemini_client("{}")[0]).counts_toward_budget is True


# ---- make_provider ----

@pytest.mark.parametrize("provider", ["gemini", "anthropic", "openai"])
def test_a_missing_api_key_is_a_clear_message(provider):
    with pytest.raises(ProviderUnavailable, match=f"no API key.*{provider.upper()}_API_KEY"):
        make_provider(config(provider, api_key=None))


@pytest.mark.parametrize("provider", ["gemini", "anthropic", "openai"])
def test_a_missing_sdk_is_a_clear_message_naming_the_install_command(provider, monkeypatch):
    monkeypatch.setitem(sys.modules, SDK_MODULES[provider], None)  # import now fails as if not installed
    with pytest.raises(ProviderUnavailable, match=rf'pip install -e "\.\[{provider}\]"'):
        make_provider(config(provider))


def test_ollama_needs_no_key():
    p = make_provider(config("ollama", api_key=None, ollama_url="http://box:11434"))
    assert isinstance(p, OllamaProvider) and p.url == "http://box:11434"


def test_make_provider_refuses_offline_config():
    with pytest.raises(ProviderUnavailable):
        make_provider(config("none"))


# ---- the SDKs are optional ----

def test_the_package_imports_with_every_provider_sdk_missing(monkeypatch):
    for module in SDK_MODULES.values():
        monkeypatch.setitem(sys.modules, module, None)
    for name in [m for m in sys.modules if m == "scheduler" or m.startswith("scheduler.")]:
        monkeypatch.delitem(sys.modules, name)  # restored after the test
    for name in ["scheduler", "scheduler.parsing", "scheduler.parsing.router", "scheduler.parsing.llm",
                 "scheduler.parsing.providers.gemini", "scheduler.parsing.providers.anthropic",
                 "scheduler.parsing.providers.openai", "scheduler.parsing.providers.ollama"]:
        importlib.import_module(name)
