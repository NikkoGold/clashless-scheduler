"""Setup through the web API: the same user-wide settings file as the CLI. The key never comes back."""
import pytest

from tests.helpers.api_client import api, ok, parse, sample
from tests.helpers.fake_provider import FakeProvider, reply

VAGUE = "I'm not a morning person"


def setup(c, **body):
    return c.post("/api/setup", json=body)


def test_with_nothing_set_up_setup_is_needed(fresh_settings):
    j = ok(api().get("/api/setup"))
    assert j["needs_setup"] is True and j["provider"] == "none" and j["source"] is None and j["has_key"] is False


def test_the_providers_are_listed_with_what_each_needs(fresh_settings):
    providers = {p["name"]: p for p in ok(api().get("/api/setup"))["providers"]}
    assert providers["gemini"]["default_model"] == "gemini-3.5-flash-lite" and providers["gemini"]["needs_key"]
    assert providers["ollama"]["default_model"] is None and not providers["ollama"]["needs_key"]
    assert "none" in providers


def test_continue_offline_is_saved_and_parsing_runs_offline(fresh_settings):
    c = api()
    ok(setup(c, provider="none"))
    assert "LLM_PROVIDER=none" in fresh_settings.saved()
    assert ok(c.get("/api/setup"))["needs_setup"] is False
    j = parse(c, sample(), "no classes before 10")
    assert j["path"] == "rules (offline)" and "use Setup" in j["path_label"] and "python -m" not in j["path_label"]


def test_a_provider_and_key_are_saved_and_the_key_never_comes_back(fresh_settings):
    c = api()
    saved = ok(setup(c, provider="gemini", api_key="secret-key-123"))
    assert "GEMINI_API_KEY=secret-key-123" in fresh_settings.saved()
    j = ok(c.get("/api/setup"))
    assert (j["provider"], j["model"], j["has_key"]) == ("gemini", "gemini-3.5-flash-lite", True)
    assert j["source"] == str(fresh_settings.user_file.resolve())
    assert "secret-key-123" not in str(saved) + str(j)


def test_an_empty_key_keeps_the_saved_one(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=old\n")
    ok(setup(api(), provider="gemini", model="gemini-other", api_key=""))
    assert "GEMINI_API_KEY=old" in fresh_settings.saved() and "LLM_MODEL=gemini-other" in fresh_settings.saved()


def test_another_provider_replaces_the_choice(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=gemini\nGEMINI_API_KEY=g\n")
    ok(setup(api(), provider="anthropic", api_key="a"))
    assert fresh_settings.saved().count("LLM_PROVIDER=") == 1 and "LLM_PROVIDER=anthropic" in fresh_settings.saved()


@pytest.mark.parametrize("body, says", [
    ({"provider": "gemeni", "api_key": "k"}, "gemeni"),
    ({"provider": "openai", "api_key": "k"}, "model"),
    ({"provider": "ollama"}, "model"),
])
def test_a_bad_setup_is_refused_and_writes_nothing(fresh_settings, body, says):
    r = setup(api(), **body)
    assert r.status_code == 400 and says in r.json()["error"]
    assert fresh_settings.saved() == ""


def test_a_project_env_override_is_reported(fresh_settings):
    fresh_settings.save("LLM_PROVIDER=none\n", project=True)
    c = api()
    assert ok(c.get("/api/setup"))["source"] == str(fresh_settings.project_env.resolve())
    saved = ok(setup(c, provider="gemini", api_key="k"))
    assert "overrides" in saved["warning"]


def test_a_parse_right_after_setup_uses_the_new_provider(fresh_settings):
    fake = FakeProvider([reply({"kind": "no_class_before", "time": "10:00", "strength": "soft"})])
    c = api(provider=fake)
    assert parse(c, sample(), VAGUE)["path"] == "rules (offline)"
    ok(setup(c, provider="gemini", api_key="k"))
    assert parse(c, sample(), VAGUE)["path"] == "llm" and len(fake.calls) == 1


def test_settings_from_the_environment_are_named_without_their_values(fresh_settings, monkeypatch):
    fresh_settings.save("LLM_PROVIDER=gemini\n")
    monkeypatch.setenv("GEMINI_API_KEY", "env-secret")
    j = ok(api().get("/api/setup"))
    assert j["from_environment"] == ["GEMINI_API_KEY"] and "env-secret" not in str(j)
