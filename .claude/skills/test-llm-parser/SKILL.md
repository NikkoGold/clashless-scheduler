---
name: test-llm-parser
description: Test-first workflow for the scheduler's LLM parsing path — provider adapters (parsing/providers/*), LLMParser (parsing/llm.py), the router (parsing/router.py) and config.py — using a fake provider, never real API calls. Covers validation and retry, rules-first routing, offline mode, setup writing .env, daily budget, caching and lazy SDK imports. Use when starting build step 4 or changing any of those files.
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
- Round-trip property (Hypothesis, `problems()` strategy): any valid constraints sent the way a model might (defaults left out, other parameters null, names in any case) come back unchanged, with one call. Widen it when a constraint kind or parameter is added.
- Names: an exact match wins over a case-insensitive one (catalog ids may differ only in case).

## Router (`test_router.py`)
Rules first, always (see plan.md Parsing → Router):
- Text the RuleParser fully parses → **zero** provider calls, path `rules`, even with an LLM configured.
- Text with something unparsed + LLM configured → exactly one provider call, with the **whole** text (not just the leftover clauses); path `llm`.
- No `.env` at all / `LLM_PROVIDER=none` → works, path `rules (offline)`, unparsed shown, no provider constructed.

Uses the rules result (unparsed shown) and reports why the LLM wasn't used when:
- no API key for the selected provider
- provider raises (network/API error)
- daily call limit (`LLM_MAX_CALLS_PER_DAY`) reached — use an injectable clock/counter, not real dates

Also:
- Cache: same text + same catalog → second call makes no provider call; changed catalog → cache miss.
- Config: setup writes `.env` (provider, model, key) and "continue offline" writes `LLM_PROVIDER=none`; use `tmp_path`, never the real `.env`.

## Providers / config
- Importing `scheduler` and `scheduler.parsing` works without any provider extras installed (simulate with `monkeypatch.setitem(sys.modules, "<sdk>", None)`).
- Selecting a provider whose SDK is missing gives a clear message, then falls back.
- Each adapter: given a canned SDK response object, returns the JSON payload (mock the SDK client, not HTTP).
- `config.py` reads `.env` values and defaults; test with `monkeypatch`, never a real `.env`.

## Live eval (opt-in, real provider)
The only test that calls a real API. `tests/test_llm_live.py`, marked `live`, skipped unless `RUN_LIVE_LLM=1`.
- Input: entries in `tests/data/phrases.yaml` that have an `llm:` expectation, parsed against `tests/data/phrase_catalog.json`. Keep at most 17 (the call limit minus room for retries, checked offline), covering the main constraint kinds, with at least half being phrases the rules leave unparsed (also checked offline): that is what the LLM gets in real use. Only Gemini is evaluated live (the provider the project uses); the other adapters are covered by the mocked tests.
- Calls the real **LLMParser directly** (not the router: rules-first would answer the simple phrases without the LLM), with no cache; stops after `LIVE_LLM_MAX_CALLS` calls (default 20, separate from the app's daily limit).
- A phrase passes when its constraints match the `llm:` expectation exactly (unordered; `unparsed` ignored). Every phrase must pass, and each failure is traced to its root cause, never waved through. Two tiers: *clear* (the rules read the whole phrase) is always all-or-nothing; *hard* (the rules leave some unparsed) must reach `LIVE_LLM_MIN_ACCURACY`, default 1.0, which may be lowered only temporarily while a known flaky phrase is fixed. The provider runs at temperature 0, so results should be close to deterministic. A phrase skipped at the call limit fails the run.
- Print a per-phrase table: pass/fail, tier, expected, what the model returned. A provider error counts as a fail for that phrase.
- Run with `$env:RUN_LIVE_LLM=1; pytest -m live -s; Remove-Item Env:RUN_LIVE_LLM` **only** after a change that affects it: the prompt, the output schema, the conventions the prompt states, the `llm:` phrases, the Gemini adapter, switching provider/model, or Google retiring a model. Never re-run it just because other tests or code changed, and don't run it again to re-check a pass: each run costs up to 20 calls of the free-tier quota. The flag stays set in the PowerShell session, so the command clears it. (The default model is pinned, since the v1 API has no `-latest` aliases.) The run stops at the first provider error, so an outage or a wrong model name costs one call.
