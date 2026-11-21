"""The router: rules first, always; the whole text to the LLM only when the rules leave something and an LLM is set up.

1. The RuleParser reads the whole text. If it understood everything, that's the result: no LLM call.
2. Offline (no LLM set up): the rules result, with a note that free-form text needs an LLM.
3. Otherwise the whole-text cache, then the LLM with the whole text (never just the leftover clauses).
4. If the LLM can't be used (no key, SDK missing, daily limit, an error, invalid replies), the rules result is
   used and the path says why.
The cache and the daily call count are small files in the user-wide folder (config.cache_dir()), so they also work
across CLI runs and folders. Several tabs of the web page share them: each file's read-then-write happens under a
lock, so parallel requests can't both pass the daily limit or drop each other's cache entries. See plan.md, Router.
"""
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ValidationError

from scheduler.config import Config, cache_dir as default_cache_dir
from scheduler.models import Catalog
from scheduler.parsing.base import ParseResult
from scheduler.parsing.llm import LLMFailed, LLMParser
from scheduler.parsing.providers import Provider, ProviderUnavailable, make_provider
from scheduler.parsing.rules import RuleParser

CACHE_SIZE = 200
OFFLINE_NOTE = ("Offline: only simple, direct phrasing is understood. "
                "Set up an LLM (python -m scheduler setup) to have free-form text read as well.")


class RoutedParse(BaseModel):
    result: ParseResult
    # "cache", "rules", "llm", "rules (offline)" or "rules (LLM unavailable: <reason>)"; the UI and CLI show it
    path: str
    note: str | None = None  # anything else the user should know: the offline notice, a setting that couldn't be used


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
REPLACE_TRIES = 10  # Windows refuses to replace a file another process is reading at that moment: wait briefly


def _lock(path: Path) -> threading.Lock:
    """One lock per file, shared by every Router in this process (each web request makes its own Router)."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(Path(path).resolve(), threading.Lock())


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data) -> None:
    """Write through a temporary file of its own, then move it into place: never a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class DailyBudget:
    """Real LLM calls made today (local date), retries included. A missing or corrupt file counts as zero."""

    def __init__(self, path: Path, limit: int, today: Callable[[], date]):
        self.path, self.limit, self.today = path, limit, today

    def _count(self) -> int:
        data = _read_json(self.path)
        if isinstance(data, dict) and data.get("date") == self.today().isoformat() and isinstance(data.get("count"), int):
            return data["count"]
        return 0

    def used(self) -> int:
        with _lock(self.path):
            return self._count()

    def try_spend(self) -> bool:
        with _lock(self.path):  # check and count in one step: parallel requests can't both take the last call
            count = self._count()
            if count >= self.limit:
                return False
            _write_json(self.path, {"date": self.today().isoformat(), "count": count + 1})
            return True


class ParseCache:
    """LLM results by whole-text key, most recent last. A corrupt file is ignored."""

    def __init__(self, path: Path, size: int):
        self.path, self.size = path, size

    def _load(self) -> dict:
        data = _read_json(self.path)
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> ParseResult | None:
        with _lock(self.path):
            entry = self._load().get(key)
        try:
            return ParseResult.model_validate(entry) if entry is not None else None
        except ValidationError:
            return None

    def put(self, key: str, result: ParseResult) -> None:
        with _lock(self.path):  # read, add and write in one step, so parallel puts don't drop each other's entries
            entries = self._load()
            entries.pop(key, None)
            entries[key] = result.model_dump(mode="json")
            while len(entries) > self.size:
                entries.pop(next(iter(entries)))
            _write_json(self.path, entries)


def cache_key(text: str, catalog: Catalog, config: Config) -> str:
    raw = json.dumps([text, catalog.model_dump_json(), config.provider, config.model])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Router:
    def __init__(self, config: Config, cache_dir: Path | None = None,
                 provider_factory: Callable[[Config], Provider] = make_provider,
                 today: Callable[[], date] = date.today, cache_size: int = CACHE_SIZE):
        self.config = config
        self.provider_factory = provider_factory
        self.rules = RuleParser()
        cache_dir = default_cache_dir() if cache_dir is None else cache_dir
        self.cache = ParseCache(Path(cache_dir) / "parse_cache.json", cache_size)
        self.budget = DailyBudget(Path(cache_dir) / "llm_usage.json", config.max_calls_per_day, today)

    def parse(self, text: str, catalog: Catalog) -> RoutedParse:
        rules = self.rules.parse(text, catalog)
        if self.config.provider == "none":
            notes = [self.config.problem, OFFLINE_NOTE if rules.unparsed else None]
            return self._routed(rules, "rules (offline)", *notes)
        if not rules.unparsed:
            return self._routed(rules, "rules", self.config.problem)

        key = cache_key(text, catalog, self.config)
        cached = self.cache.get(key)
        if cached is not None:
            return self._routed(cached, "cache", self.config.problem)
        try:
            provider = self.provider_factory(self.config)  # built only when it's needed
            budget = self.budget if provider.counts_toward_budget else None
            result = LLMParser(provider, budget=budget).parse(text, catalog)
        except (ProviderUnavailable, LLMFailed) as e:
            return self._routed(rules, f"rules (LLM unavailable: {e})", self.config.problem)
        self.cache.put(key, result)
        return self._routed(result, "llm", self.config.problem)

    @staticmethod
    def _routed(result: ParseResult, path: str, *notes: str | None) -> RoutedParse:
        return RoutedParse(result=result, path=path, note=" ".join(n for n in notes if n) or None)
