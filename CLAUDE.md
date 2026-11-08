# Class Scheduler

## Goal
Build weekly class schedules from a JSON catalog of courses and section times, plus the user's constraints and preferences written in plain language. A rule-based parser turns the text into structured constraints, and a configurable LLM (Gemini Flash-Lite recommended) is called only for text the rules can't fully parse. No model or key ships with the app: first-run setup configures one, or the user continues in a more limited offline mode (rules only). A CP-SAT solver returns the top-K schedules with no clashes. The app is mainly used through a local web UI, and there is also a CLI.

The full design is in [plan.md](plan.md). Keep this file and plan.md in sync when the design changes.

## Stack
Python 3.11+, FastAPI and uvicorn, a plain HTML/JS/CSS frontend (no build step), Pydantic v2, OR-Tools CP-SAT, openpyxl, python-dotenv. Each LLM provider's SDK is an optional extra and is only imported when that provider is selected.

## Architecture
```
plain text ──► parsing/router ──► ParseResult{constraints, unparsed} ──► (optional review) ──► solver ──► top-K schedules ──► UI / CLI / export
                 ├─ RuleParser (always first; the only parser offline)
                 └─ LLMParser (providers/: gemini, anthropic, openai, ollama), only if rules leave text unparsed
courses.json ──► catalog (Pydantic) ─────────────────────────────────────────────────────────► solver
```
- `scheduler/models.py`: Catalog and Constraint schemas. These are the single source of truth for both parsers and the solver.
- `scheduler/catalog.py`: loads and checks the JSON, time helpers, merges groups that meet at identical times.
- `scheduler/constraints.py`: the constraint kinds, and how each one is added to the CP-SAT model.
- `scheduler/solver.py`: builds the model, top-K enumeration, explains unsolvable cases, score breakdown.
- `scheduler/parsing/`: `base.py` (protocol), `rules.py`, `llm.py`, `providers/`, `router.py` (whole-text cache → rules first → whole text to the LLM only if something is unparsed and an LLM is set up; budget; fallback with the reason shown; records the path used). A phrase-level cache was considered and dropped; see plan.md Parsing → Router.
- `scheduler/config.py`: reads `.env` (`LLM_PROVIDER`, `LLM_MODEL`, API keys, `LLM_MAX_CALLS_PER_DAY`) and writes it from first-run setup. No `.env` means offline.
- `scheduler/cli.py`: `python -m scheduler courses.json "prefs" [--review] [--pick N:IDS] [--no-required] [--offline] [--top 5] [--export out.xlsx]`, and `python -m scheduler setup`.
- `scheduler/web/`: `app.py` (FastAPI `/api/catalog`, `/api/parse`, `/api/solve`, `/api/export/xlsx`) and `static/`.

## Core rules (don't break these)
- **The user is always fully informed about a schedule they're shown.** Every result shows every rule, hard and soft, as met or broken. Trade-offs are stated ("Register for 11, or 12, which breaks X instead of Y"), and when nothing fits, the conflicting rules are named. Every interface must carry this through.
- **The LLM only produces constraints.** It never decides the schedule. The solver is deterministic.
- **Both parsers return the same `ParseResult`.** Anything not understood goes in `unparsed` as the original text and is always shown to the user. The rule parser never guesses.
- **Rules first, always.** The LLM is never called when the rule parser understands the whole text. Offline mode (no LLM set up) must work fully, and it tells the user it's limited.
- **Back-to-back classes are fine.** The overlap test is `a.start < b.end and b.start < a.end`. There is no travel time and no online/in-person handling.
- **Exactly one group per component** for each selected course. Any lab can go with any lecture (the `parent` field is stored but not enforced).
- **Top-K never repeats.** Each solution found is ruled out before solving again. Stop at K solutions or when there are none left, and report "only N possible". Groups with identical times are merged first, so no two results look the same on the grid.
- **`take_n_of`**: if the pool has exactly n courses, take all of them. If it has fewer than n, show an error before solving. n only counts courses in that pool; a required course in the pool uses up a pick. Hard means exactly n. Soft means n is the target, and each course over costs twice what each course short costs.
- **Time limit: 3 minutes per request.** On timeout, keep the results found (each proven best), set `timed_out`, and never claim anything unproven. The UI shows a loading indicator meanwhile.
- **Conflicts are minimal and name the right rules.** The conflict search keeps the full request's courses available and treats "at least one course" as a droppable rule (`nothing_to_schedule`); see plan.md Solver. Check changes against the oracle-based conflict property tests.
- The week defaults to **Sun–Fri** (it can be changed with the catalog's `week` field).
- **Session times are whole hours.** The loader rejects `10:30`.
- **`required` (course) is the data-file form of `must_take`.** `default_constraints(catalog)` turns each required course into a hard `must_take`; the solver never reads `required`. Courses in neither a `must_take` nor a `take_n_of` pool are not scheduled.
- The `attendance_required` field on groups (default `true`; "do I have to attend") is checked but **not used** in v1. It's kept for the deferred `max_overlaps` feature.
- `data/sample_courses.json` is hand-written (see `data/README.md`). Don't assert exact results against it.

## Not in v1 (don't build these unless asked)
- `link_groups`: only allow labs whose `parent` matches the chosen lecture.
- `max_overlaps(n)`: allow clashes with classes marked `attendance_required: false`.
- `.ics` export, travel time, online vs in-person.

## Commands (Windows / PowerShell)
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev,gemini]"        # other extras: anthropic, openai
python -m scheduler setup              # choose a provider + key, or continue offline (or copy .env.example .env)
uvicorn scheduler.web.app:app --reload
python -m scheduler data/sample_courses.json "no classes before 10, keep 12-13 free"
pytest -q                              # offline suite
$env:RUN_LIVE_LLM=1; pytest -m live    # opt-in real-LLM eval
```

## Testing (pytest)
All tests use **pytest** and are written before the code. The detailed workflows are in the project skills under `.claude/skills/`, and the shared rules are in [.claude/skills/test-core-logic/conventions.md](.claude/skills/test-core-logic/conventions.md). Use the matching skill whenever you touch that part of the code:

| Area | Skill |
|---|---|
| models, catalog, constraints, solver (build steps 1–2); owns `tests/helpers/` | `test-core-logic` |
| RuleParser, `tests/data/phrases.yaml` (step 3) | `test-rule-parser` |
| LLMParser, providers, router, config (step 4) | `test-llm-parser` |
| CLI, API, Excel/JSON/PDF export (steps 5–7) | `test-interfaces` |
| adding or changing a constraint kind | `add-constraint-kind` |

Key rules:
- **Test-first:** turn the build step's Verification bullets in plan.md into named failing tests, then write the code, then run `pytest -q`.
- **`pytest -q` must pass offline**, with no network, no API keys and no `.env`. Mock LLMs with `tests/helpers/fake_provider.py`. Set config with `monkeypatch`.
- Reuse the shared helpers in `tests/helpers/` (catalog builder, `assert_valid_schedule`/`assert_valid_results` invariant checker, brute-force oracle). Don't redefine them inside test files.
- Every solver test runs its results through the invariant checker.
- Rule parser cases go in `tests/data/phrases.yaml` (phrase → expected constraints and unparsed text). Optional `llm:` expectations feed the live eval.
- Markers: `slow` (runs by default) and `live` (real LLM calls, skipped unless `RUN_LIVE_LLM=1`, capped by `LIVE_LLM_MAX_CALLS`, default 40). The live eval is the only test that may use the network. Run it with `RUN_LIVE_LLM=1 pytest -m live` after any prompt, schema or model change.
- Name tests after behaviour (`test_back_to_back_classes_do_not_clash`), one behaviour per test, and parametrize variants. Use `tmp_path` for written files.
- Solver behaviours that must have tests (from plan.md):
  - back-to-back 10–12 / 12–14 is allowed
  - a multi-session group is all-or-nothing: a clash with only its second session rules out the whole group
  - a known best answer
  - an unsolvable case names the conflicting constraints
  - top-K results are all different
  - a catalog with only 2 valid schedules and K=5 returns exactly 2 with the "only 2" note
  - two labs at identical times merge into one option
  - pick-N where the pool equals N takes all courses
  - any lab can go with any lecture
  - `attendance_required: false` doesn't change the result
- New constraint kinds or parser patterns need tests in the same change.
- **Guard against blind spots** (each of these once hid a real bug):
  - Generate combinations instead of hand-picking examples (parser verbs × list forms × both catalogs).
  - Parser phrases run against the realistic sample catalog too (`catalog: sample`), not only the tidy phrase catalog.
  - Hypothesis strategies must vary everything the code depends on: mixed hard/soft requests, different weeks, constraint times off the hour. Widen them when a feature adds a new dimension.
- **End-to-end scenarios** (`tests/test_end_to_end.py`): text → parser → picker defaults → solver on the sample catalog. Steps 5 and 6 must add the same scenarios through the CLI and the API.
