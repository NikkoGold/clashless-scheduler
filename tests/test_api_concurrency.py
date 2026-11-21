"""Several tabs at once: the server keeps no per-user state, so requests run side by side.

The parse cache and the daily LLM count are shared files in the user-wide folder, written by every tab.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from scheduler.catalog import load_catalog
from scheduler.config import Config, cache_dir
from scheduler.parsing.router import Router
from tests.helpers.api_client import api, parse, rules_of, sample, solve
from tests.helpers.cli_runner import SAMPLE
from tests.helpers.fake_provider import reply

pytestmark = pytest.mark.usefixtures("fresh_settings")

TEXTS = ["no classes before 10", "keep 12-13 free", "prefer Cohen", "compact schedule", "no classes after 18",
         "ideally no classes before 9", "avoid Brenner", "at most 5 days"]


def test_solves_sent_at_the_same_time_all_succeed_and_match_running_them_one_by_one():
    def one(text):
        c = api()  # each tab has its own connection
        return solve(c, sample(), rules_of(parse(c, sample(), text)), modes={"ZGC": "off"}, top=3)["result"]

    alone = [one(t) for t in TEXTS]
    with ThreadPoolExecutor(max_workers=len(TEXTS)) as pool:
        together = list(pool.map(one, TEXTS))
    assert together == alone


class SlowProvider:
    """A fake LLM that takes a moment, so calls from different threads overlap. Counts real calls."""
    name, model, model_version, counts_toward_budget = "fake", "fake-1", "fake-1", True

    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def complete(self, system, user, schema):
        with self.lock:
            self.calls += 1
        time.sleep(0.02)
        return json.dumps(reply({"kind": "no_class_before", "time": "10:00", "strength": "soft"}))


def parse_together(n: int, limit: int, provider) -> list[str]:
    cat = load_catalog(SAMPLE)
    config = Config(provider="gemini", model="m", api_key="k", max_calls_per_day=limit)

    def one(i):
        # different text each time, so each needs the LLM (the rules can't read it) and writes its own cache entry
        return Router(config, provider_factory=lambda c: provider).parse(f"I'm not a morning person {i}", cat).path

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(n)))


def test_parses_writing_the_shared_cache_at_the_same_time_never_fail():
    paths = parse_together(16, limit=100, provider=SlowProvider())
    assert paths == ["llm"] * 16
    entries = json.loads((cache_dir() / "parse_cache.json").read_text(encoding="utf-8"))
    assert isinstance(entries, dict) and len(entries) == 16
    assert list(cache_dir().glob("*.tmp")) == []  # every write's own temporary file was moved into place


def test_the_daily_limit_holds_under_parallel_calls():
    provider = SlowProvider()
    paths = parse_together(16, limit=5, provider=provider)
    assert provider.calls <= 5
    assert paths.count("llm") == provider.calls
    assert all(p == "llm" or "limit" in p for p in paths)
    usage = json.loads((cache_dir() / "llm_usage.json").read_text(encoding="utf-8"))
    assert usage["count"] == provider.calls
