---
name: test-interfaces
description: Test-first workflow for the scheduler's user-facing layers — the CLI (cli.py), FastAPI endpoints (web/app.py) and exports (Excel via openpyxl, JSON, PDF print stylesheet). Use when starting build steps 5, 6 or 7, or changing CLI flags, API routes or export output. Frontend and PDF are covered by a manual checklist.
---

# CLI, API and export tests (build steps 5–7)

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). Build input catalogs with the catalog builder and write them to `tmp_path` when a file is needed. Force `--offline` / `LLM_PROVIDER=none` so nothing hits the network.

## End-to-end scenarios (required in steps 5 and 6)
`tests/test_end_to_end.py` runs real-use scenarios on the sample catalog (text → parser → picker defaults → solver). Each interface step must run **the same scenarios through its own interface**: the CLI in step 5 (`main(argv)` on `data/sample_courses.json`), the API in step 6 (`TestClient`: catalog → parse → solve). Assert the same outcomes (the documented counts, the ZGC conflict, unparsed fragments reported, every rule in the breakdown). Until the UI exists, these are the only tests that simulate real use.

## CLI (built in step 5)
Run it in-process with `tests/helpers/cli_runner.py`: `cli(path, "prefs", "--offline", console=FakeConsole([...]), provider=fake)` returns the exit code, stdout, stderr and the `Outcome` (catalog, constraints, routed parse, `SolveResult`). Run every solved result through `assert_valid_results`, so tests check schedules, not just wording. Mark CLI test modules with `pytestmark = pytest.mark.usefixtures("fresh_settings")`: each test gets its own empty user-wide folder and working folder (a true first run; `fresh_settings.save(...)` writes settings). Typed answers, hidden answers (getpass), "is a terminal" and stdin all come from `tests/helpers/fake_console.py`.
- `test_cli.py`: flags (`--top` 1-20, `--pick [N:]IDS`, `--drop`, `--no-required`, `--offline`, `-` for stdin), what's printed (courses, parser path, settings source, rules, unparsed advice, sessions, merged "11 or 12", register notes, differences from #1, broken rules or `--verbose` all rules), exit codes, and readable errors.
- `test_cli_review.py`: the `Review` commands, including several numbers at once equalling one command each, invalid commands changing nothing, and a Hypothesis test of random command sequences against a reference model.
- `test_cli_setup.py`: first run (terminal question vs one-line notice), setup per provider, keys read hidden and never printed, Enter keeps a saved key, cancelling writes nothing.
- `test_end_to_end_cli.py`: the end-to-end scenarios through the CLI, review sessions, and generated flag × text × catalog combinations.
- `test_cli_subprocess.py`: a few real `python -m scheduler` runs for the entry point and exit codes.

## API (built in step 6)
Use `tests/helpers/api_client.py`: `api(provider=fake)` is a `TestClient` on `create_app()` with Host 127.0.0.1 (the default `testserver` Host is refused, on purpose); `parse()` and `solve()` call the API like the page does, and `solve()` runs every result through the invariant checker. Mark modules with `pytestmark = pytest.mark.usefixtures("fresh_settings")`.
- `test_api.py`: catalog, parse, rules and solve, the display text, and errors always being `{"error": ...}` with a 4xx.
- `test_api_setup.py`: setup through the API; the key never comes back.
- `test_api_security.py`: Host check on every route, JSON-only POSTs, no CORS headers.
- `test_api_concurrency.py`: parallel solves, and parallel writes to the shared cache and daily count.
- `test_end_to_end_api.py`: the end-to-end scenarios, a review session, and generated combinations.
- `test_web_command.py`: `python -m scheduler web` (127.0.0.1, next free port, browser), with uvicorn and the browser recorded, not started.
- `test_selection.py`: the course choice shared by the CLI and the picker.
- Step 7 adds `/api/export/xlsx`: correct content type and a loadable workbook.

## Exports (`test_export.py`)
- **Excel**: open with `openpyxl.load_workbook`. Sheet 1: weekly grid covering the catalog's `week`, cells for each session filled with that course's colour. Sheet 2: one row per session with course, component, group, day, time, room, instructor.
- **JSON**: export → reload gives an equal solution.

## Manual checklist (report in the summary, don't automate)
- First run with no `.env`: setup panel appears; "Continue offline" explains the limits, dismisses the panel for good, and setup can be reopened.
- Parser-path label shown (`rules`, `llm`, `rules (offline)`, `rules (LLM unavailable: …)`).
- Course search filters by id, name, instructor while typing.
- required / in pool / off toggle and "pick N" box.
- Review checkbox on → editable chips + Solve; off → straight to results.
- Unparsed fragments highlighted under the text box.
- Merged options shown as "lab 11 or 12"; tabs show differences from #1.
- Print preview (PDF) shows only the selected grid and course list.
