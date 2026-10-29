# Scheduler test conventions (shared by all scheduler test skills)

## Test-first loop (every build step and every change)
1. Read the build step in `plan.md` and the matching Verification bullets.
2. Turn each bullet into a named test. Run it and confirm it fails **for the right reason** (missing behaviour, not an import typo).
3. Implement until it passes.
4. Run the full suite: `pytest -q`.
5. Report which Verification bullets are now covered and which remain.

When fixing a bug: write the failing test that reproduces it first.

## Layout
```
tests/
  conftest.py
  helpers/catalog_builder.py   # owned by test-core-logic
  helpers/invariants.py        # owned by test-core-logic
  helpers/oracle.py            # owned by test-core-logic
  helpers/strategies.py        # owned by test-core-logic (after step 2)
  helpers/fake_provider.py     # owned by test-llm-parser
  data/phrases.yaml            # owned by test-rule-parser, read by the live LLM eval
  data/phrase_catalog.json     # fixed catalog the phrases are parsed against
  test_catalog.py
  test_solver_*.py
  test_solver_properties.py
  test_rule_parser.py
  test_llm_parser.py
  test_llm_live.py
  test_router.py
  test_cli.py
  test_api.py
  test_export.py
```
Helpers are created once and reused. Never redefine a builder or checker inside a test file; extend the helper instead.

## Rules
- `pytest -q` must pass offline: no network, no API keys, no `.env` needed. Use `monkeypatch.setenv`/`delenv` for config.
- Markers (registered in `pyproject.toml` under `[tool.pytest.ini_options]`):
  - `slow` — longer solver runs; still run by default.
  - `live` — real LLM calls; skipped unless `RUN_LIVE_LLM=1`. Capped by `LIVE_LLM_MAX_CALLS` (default 40), separate from the app's `LLM_MAX_CALLS_PER_DAY`.
- Test names describe behaviour: `test_back_to_back_classes_do_not_clash`, not `test_overlap_2`.
- One behaviour per test. Parametrize variants instead of looping inside a test.
- Use `tmp_path` for any files written (exports, catalogs).
