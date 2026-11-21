"""Router: rules first, the whole text to the LLM only when needed, fallbacks with the reason, the daily limit and the cache.

Every test uses tmp_path for the cache and usage files, and a fake provider (or a real adapter with a mocked client).
"""
import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

from scheduler.catalog import load_catalog
from scheduler.config import Config, load_config
from scheduler.parsing.providers import make_provider
from scheduler.parsing.providers.ollama import OllamaProvider
from scheduler.parsing.router import CACHE_SIZE, OFFLINE_NOTE, Router, RoutedParse, cache_key
from scheduler.parsing.rules import RuleParser
from tests.helpers.fake_provider import FakeProvider, StatusError, reply

PHRASE_CATALOG = Path(__file__).parent / "data" / "phrase_catalog.json"
CLEAR = "no classes before 10, fridays off"  # the rule parser understands all of it
VAGUE = "fridays off, I'm not a morning person"  # the second clause needs the LLM
LLM_REPLY = reply({"kind": "day_off", "day": "Fri"}, {"kind": "no_class_before", "time": "10:00", "strength": "soft"})
TODAY = date(2026, 11, 14)


@pytest.fixture(scope="module")
def cat():
    return load_catalog(PHRASE_CATALOG)


def gemini(**kw) -> Config:
    return Config(provider="gemini", model="gemini-3.5-flash-lite", api_key="k", **kw)


class Factory:
    """provider_factory for the router: hands out one fake, and records how often a provider was built."""

    def __init__(self, provider):
        self.provider = provider
        self.built = 0

    def __call__(self, config):
        self.built += 1
        return self.provider


def router(tmp_path, *replies, config=None, today=TODAY, provider=None, **kw):
    fake = provider or FakeProvider(list(replies))
    factory = Factory(fake)
    r = Router(config or gemini(), cache_dir=tmp_path / ".cache", provider_factory=factory, today=lambda: today, **kw)
    return r, fake, factory


def usage(tmp_path) -> dict:
    return json.loads((tmp_path / ".cache" / "llm_usage.json").read_text(encoding="utf-8"))


def set_usage(tmp_path, day: date, count: int):
    (tmp_path / ".cache").mkdir(exist_ok=True)
    (tmp_path / ".cache" / "llm_usage.json").write_text(json.dumps({"date": day.isoformat(), "count": count}), encoding="utf-8")


# ---- rules first ----

def test_text_the_rules_fully_parse_never_reaches_the_llm(tmp_path, cat):
    r, fake, factory = router(tmp_path)
    routed = r.parse(CLEAR, cat)
    assert isinstance(routed, RoutedParse)
    assert routed.path == "rules" and routed.note is None
    assert routed.result == RuleParser().parse(CLEAR, cat)
    assert factory.built == 0 and fake.calls == []  # not even constructed


def test_text_with_something_unparsed_sends_the_whole_text_to_the_llm_once(tmp_path, cat):
    r, fake, _ = router(tmp_path, LLM_REPLY)
    routed = r.parse(VAGUE, cat)
    assert routed.path == "llm"
    assert len(fake.calls) == 1
    assert fake.calls[0]["user"].rstrip().endswith(VAGUE)  # the whole text, not just "I'm not a morning person"
    assert [c.kind for c in routed.result.constraints] == ["day_off", "no_class_before"]


# ---- offline ----

def test_no_env_file_at_all_runs_offline_without_building_a_provider(tmp_path, cat):
    cfg = load_config(tmp_path / ".env", environ={})
    r, fake, factory = router(tmp_path, config=cfg)
    routed = r.parse(VAGUE, cat)
    assert routed.path == "rules (offline)"
    assert routed.result == RuleParser().parse(VAGUE, cat)
    assert [f.text for f in routed.result.unparsed] == ["I'm not a morning person"]
    assert routed.note == OFFLINE_NOTE and "LLM" in OFFLINE_NOTE
    assert factory.built == 0


def test_offline_text_that_fully_parses_has_no_note(tmp_path, cat):
    r, _, _ = router(tmp_path, config=Config(provider="none"))
    routed = r.parse(CLEAR, cat)
    assert routed.path == "rules (offline)" and routed.note is None


def test_a_config_problem_is_shown_with_the_offline_result(tmp_path, cat):
    cfg = load_config(_env(tmp_path, "LLM_PROVIDER=gemeni\n"), environ={})
    routed = router(tmp_path, config=cfg)[0].parse(VAGUE, cat)
    assert routed.path == "rules (offline)" and "gemeni" in routed.note


def _env(tmp_path, text) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# ---- falling back to the rules result, with the reason ----

def _missing_key(tmp_path):
    return Router(Config(provider="gemini", model="m", api_key=None), cache_dir=tmp_path / ".cache", today=lambda: TODAY)


def _missing_sdk(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)
    return Router(gemini(), cache_dir=tmp_path / ".cache", today=lambda: TODAY)


def _provider_error(tmp_path):
    return router(tmp_path, ConnectionError("503 from the API"))[0]


def _invalid_twice(tmp_path):
    return router(tmp_path, "not json", reply({"kind": "day_off"}))[0]


def _limit_reached(tmp_path):
    set_usage(tmp_path, TODAY, 3)
    return router(tmp_path, LLM_REPLY, config=gemini(max_calls_per_day=3))[0]


def _ollama_down(tmp_path):
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    down = OllamaProvider(url="http://localhost:11434", model="llama3", client=httpx.Client(transport=httpx.MockTransport(refuse)))
    return router(tmp_path, provider=down, config=Config(provider="ollama", model="llama3"))[0]


@pytest.mark.parametrize("make, reason", [
    (lambda tp, mp: _missing_key(tp), "no API key"),
    (lambda tp, mp: _missing_sdk(tp, mp), "pip install"),
    (lambda tp, mp: _provider_error(tp), "503 from the API"),
    (lambda tp, mp: _invalid_twice(tp), "invalid"),
    (lambda tp, mp: _limit_reached(tp), "daily limit"),
    (lambda tp, mp: _ollama_down(tp), "connection refused"),
], ids=["missing-key", "missing-sdk", "provider-error", "invalid-twice", "limit-reached", "ollama-down"])
def test_when_the_llm_cant_be_used_the_rules_result_is_shown_with_the_reason(tmp_path, monkeypatch, cat, make, reason):
    routed = make(tmp_path, monkeypatch).parse(VAGUE, cat)
    assert routed.path.startswith("rules (LLM unavailable: ") and reason in routed.path
    assert routed.result == RuleParser().parse(VAGUE, cat)
    assert routed.result.unparsed  # what wasn't understood is still shown


# ---- the daily limit ----

def test_every_real_call_counts_retries_included(tmp_path, cat):
    r, _, _ = router(tmp_path, "not json", LLM_REPLY)
    assert r.parse(VAGUE, cat).path == "llm"
    assert usage(tmp_path) == {"date": TODAY.isoformat(), "count": 2}


def test_the_retry_is_skipped_when_the_first_call_used_the_last_one_left(tmp_path, cat):
    set_usage(tmp_path, TODAY, 2)
    r, fake, _ = router(tmp_path, "not json", LLM_REPLY, config=gemini(max_calls_per_day=3))
    routed = r.parse(VAGUE, cat)
    assert "daily limit" in routed.path and len(fake.calls) == 1
    assert usage(tmp_path)["count"] == 3


def test_a_try_after_a_503_is_counted_in_the_saved_daily_usage(tmp_path, cat, monkeypatch):
    monkeypatch.setattr("scheduler.parsing.llm.BUSY_WAITS", (0, 0, 0))  # the waits themselves are tested in test_llm_parser
    r, fake, _ = router(tmp_path, StatusError(503), LLM_REPLY)
    assert r.parse(VAGUE, cat).path == "llm" and len(fake.calls) == 2
    assert usage(tmp_path) == {"date": TODAY.isoformat(), "count": 2}


def test_a_503_on_the_last_call_left_falls_back_to_the_rules_with_the_error(tmp_path, cat, monkeypatch):
    monkeypatch.setattr("scheduler.parsing.llm.BUSY_WAITS", (0, 0, 0))
    set_usage(tmp_path, TODAY, 2)
    r, fake, _ = router(tmp_path, StatusError(503), LLM_REPLY, config=gemini(max_calls_per_day=3))
    routed = r.parse(VAGUE, cat)
    assert routed.path.startswith("rules (LLM unavailable") and "503" in routed.path
    assert len(fake.calls) == 1 and usage(tmp_path)["count"] == 3
    assert routed.result.unparsed  # what the rules couldn't read is still shown


def test_the_count_starts_again_on_a_new_day(tmp_path, cat):
    set_usage(tmp_path, date(2026, 11, 13), 3)
    r, _, _ = router(tmp_path, LLM_REPLY, config=gemini(max_calls_per_day=3))
    assert r.parse(VAGUE, cat).path == "llm"
    assert usage(tmp_path) == {"date": TODAY.isoformat(), "count": 1}


def test_ollama_calls_are_not_counted(tmp_path, cat):
    local = FakeProvider([LLM_REPLY], name="ollama", counts_toward_budget=False)
    r, _, _ = router(tmp_path, provider=local, config=Config(provider="ollama", model="llama3", max_calls_per_day=0))
    assert r.parse(VAGUE, cat).path == "llm"


@pytest.mark.parametrize("content", ["{not json", '{"date": "yesterday", "count": "many"}', "[]"])
def test_a_corrupt_usage_file_counts_as_zero(tmp_path, cat, content):
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "llm_usage.json").write_text(content, encoding="utf-8")
    r, _, _ = router(tmp_path, LLM_REPLY, config=gemini(max_calls_per_day=1))
    assert r.parse(VAGUE, cat).path == "llm"


# ---- the cache ----

def test_the_same_text_and_catalog_make_no_second_call(tmp_path, cat):
    r, fake, _ = router(tmp_path, LLM_REPLY)
    first = r.parse(VAGUE, cat)
    second = r.parse(VAGUE, cat)
    assert second.path == "cache" and second.result == first.result
    assert len(fake.calls) == 1


def test_the_cache_survives_a_new_router(tmp_path, cat):
    router(tmp_path, LLM_REPLY)[0].parse(VAGUE, cat)
    r, fake, factory = router(tmp_path)  # a new process: nothing queued, so any call would fail
    assert r.parse(VAGUE, cat).path == "cache"
    assert fake.calls == []


def _other_catalog(cat):
    data = cat.model_dump(mode="json")
    data["courses"][0]["name"] = "Intro to Programming (new)"
    return type(cat).model_validate(data)


@pytest.mark.parametrize("change", ["text", "catalog", "model", "provider"])
def test_a_changed_text_catalog_or_model_is_a_cache_miss(tmp_path, cat, change):
    router(tmp_path, LLM_REPLY)[0].parse(VAGUE, cat)
    text, catalog_, config = VAGUE, cat, gemini()
    if change == "text":
        text = VAGUE + " please"
    elif change == "catalog":
        catalog_ = _other_catalog(cat)
    elif change == "model":
        config = Config(provider="gemini", model="gemini-pro-latest", api_key="k")
    else:
        config = Config(provider="anthropic", model="gemini-3.5-flash-lite", api_key="k")
    r, fake, _ = router(tmp_path, LLM_REPLY, config=config)
    assert r.parse(text, catalog_).path == "llm" and len(fake.calls) == 1


def test_rules_results_are_not_cached(tmp_path, cat):
    router(tmp_path)[0].parse(CLEAR, cat)
    assert not (tmp_path / ".cache" / "parse_cache.json").exists()


def test_a_failed_llm_parse_is_not_cached(tmp_path, cat):
    router(tmp_path, ConnectionError("down"))[0].parse(VAGUE, cat)
    r, fake, _ = router(tmp_path, LLM_REPLY)
    assert r.parse(VAGUE, cat).path == "llm"  # tried again, not served the fallback


def test_the_cache_keeps_the_most_recent_entries(tmp_path, cat):
    assert CACHE_SIZE == 200
    texts = [f"{VAGUE} {i}" for i in range(3)]
    r, fake, _ = router(tmp_path, LLM_REPLY, LLM_REPLY, LLM_REPLY, LLM_REPLY, cache_size=2)
    for t in texts:
        r.parse(t, cat)
    assert r.parse(texts[2], cat).path == "cache"
    assert r.parse(texts[1], cat).path == "cache"
    assert r.parse(texts[0], cat).path == "llm"  # the oldest was dropped


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"k": {"constraints": "wrong"}}'])
def test_a_corrupt_cache_file_is_ignored(tmp_path, cat, content):
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "parse_cache.json").write_text(content, encoding="utf-8")
    r, _, _ = router(tmp_path, LLM_REPLY)
    assert r.parse(VAGUE, cat).path == "llm"


def test_a_bad_entry_under_the_real_key_is_ignored(tmp_path, cat):
    key = cache_key(VAGUE, cat, gemini())
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "parse_cache.json").write_text(json.dumps({key: {"constraints": "wrong"}}), encoding="utf-8")
    r, fake, _ = router(tmp_path, LLM_REPLY)
    assert r.parse(VAGUE, cat).path == "llm"
    assert r.parse(VAGUE, cat).path == "cache"  # and the good result replaced it


def test_a_cache_hit_does_not_use_the_daily_limit(tmp_path, cat):
    r, _, _ = router(tmp_path, LLM_REPLY)
    r.parse(VAGUE, cat)
    r.parse(VAGUE, cat)
    assert usage(tmp_path)["count"] == 1


def test_the_default_provider_factory_is_make_provider(tmp_path):
    assert Router(Config(provider="none"), cache_dir=tmp_path).provider_factory is make_provider


# ---- where the cache and the daily count live ----

def test_by_default_the_cache_and_daily_count_live_in_the_user_wide_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_HOME", str(tmp_path / "home"))
    r = Router(gemini())
    assert r.cache.path == tmp_path / "home" / "cache" / "parse_cache.json"
    assert r.budget.path == tmp_path / "home" / "cache" / "llm_usage.json"


def test_runs_from_two_folders_share_one_daily_count_and_one_cache(tmp_path, monkeypatch, cat):
    # one key used from many folders still gets one daily limit, and a text parsed in one is cached for the other
    monkeypatch.setenv("SCHEDULER_HOME", str(tmp_path / "home"))
    fake = FakeProvider([LLM_REPLY, reply({"kind": "day_off", "day": "Sun"})])
    paths = []
    for folder, text in [("a", VAGUE), ("b", VAGUE), ("b", "sundays off, I'm not a morning person")]:
        (tmp_path / folder).mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_path / folder)
        paths.append(Router(gemini(max_calls_per_day=1), provider_factory=lambda c: fake, today=lambda: TODAY)
                     .parse(text, cat).path)
    assert paths[:2] == ["llm", "cache"]
    assert paths[2].startswith("rules (LLM unavailable") and "limit" in paths[2]
    assert len(fake.calls) == 1
