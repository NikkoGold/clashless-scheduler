---
name: test-llm-parser
description: Test-first workflow for the scheduler's LLM parsing path — provider adapters (parsing/providers/*), LLMParser (parsing/llm.py), the router (parsing/router.py) and config.py — using a fake provider, never real API calls. Covers validation and retry, fallback to rules, daily budget, caching and lazy SDK imports. Use when starting build step 4 or changing any of those files.
---

# LLM parser and router tests (build step 4)

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). Tests here must never touch the network.

## Fake provider
Create `tests/helpers/fake_provider.py`:
- `FakeProvider(responses)` where each item is a JSON string/dict to return or an exception to raise, consumed in order.
- Records every call (`.calls`: prompt, schema) so tests can assert on what was sent and how many calls happened.
- Raises a clear error if called more times than responses queued.

## LLMParser (`test_llm_parser.py`)
- Valid structured output → constraints parsed and validated.
- Invalid output, then valid → exactly 2 calls; the second prompt contains the validation error.
- Invalid twice → handled per plan (error surfaced / fallback), no third call.
- Prompt contains the catalog's course ids, names and instructors.
- Model-returned `unparsed` fragments reach the result.

## Router (`test_router.py`)
Falls back to RuleParser, and reports `parser="rules"`, when:
- `LLM_ENABLED=false`
- no API key for the selected provider
- provider raises (network/API error)
- daily call limit (`LLM_MAX_CALLS_PER_DAY`) reached — use an injectable clock/counter, not real dates

Also:
- Success path reports `parser="llm"`.
- Cache: same text + same catalog → second call makes no provider call; changed catalog → cache miss.

## Providers / config
- Importing `scheduler` and `scheduler.parsing` works without any provider extras installed (simulate with `monkeypatch.setitem(sys.modules, "<sdk>", None)`).
- Selecting a provider whose SDK is missing gives a clear message, then falls back.
- Each adapter: given a canned SDK response object, returns the JSON payload (mock the SDK client, not HTTP).
- `config.py` reads `.env` values and defaults; test with `monkeypatch`, never a real `.env`.

## Live eval (opt-in, real provider)
The only test that calls a real API. `tests/test_llm_live.py`, marked `live`, skipped unless `RUN_LIVE_LLM=1`.
- Input: entries in `tests/data/phrases.yaml` that have an `llm:` expectation, parsed against `tests/data/phrase_catalog.json`. Aim for ~30 such phrases covering every constraint kind plus vague wording.
- Goes through the real router with the cache disabled; stops after `LIVE_LLM_MAX_CALLS` calls (default 40, separate from the app's daily limit).
- Score = share of phrases whose constraints match the `llm:` expectation exactly (unordered; `unparsed` ignored). Fail if below `LIVE_LLM_MIN_ACCURACY` (default 0.8) — results vary run to run, so it's a threshold, not per-phrase pass/fail.
- Print a per-phrase table: pass/fail, expected, what the model returned, and which parser actually answered (a silent fallback to rules counts as a fail).
- Run with `RUN_LIVE_LLM=1 pytest -m live` after any prompt or schema change, when switching provider/model, and now and then because `-latest` model aliases change underneath you.
