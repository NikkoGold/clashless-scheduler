---
name: test-interfaces
description: Test-first workflow for the scheduler's user-facing layers — the CLI (cli.py), FastAPI endpoints (web/app.py) and exports (Excel via openpyxl, JSON, PDF print stylesheet). Use when starting build steps 5, 6 or 7, or changing CLI flags, API routes or export output (scheduler/export.py). Frontend and PDF are covered by a manual checklist.
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
- `test_web_page.py`: the page's wiring without a browser: every `request("METHOD", "/api/...")` in the scripts is a real route, only `api.js` talks to the server, every file and module import exists and is served, nothing loads from the internet, the browser never stores the key, no emoji icons.
- `test_api_export.py`: `/api/export/xlsx` and `/api/export/json` for any rank: content type, file name, the file holds the schedule that was sent; a schedule that isn't the catalog's own, or a bad body, is a 4xx `{"error"}`. The export routes are in `test_api_security.py`'s route list.

## Exports (built in step 7)
- `test_export.py`, on `scheduler/export.py`. Open workbooks with `openpyxl.load_workbook` and read the grid back (`grid_blocks`) rather than checking cell addresses.
  - **Excel**: sheet 1 is the weekly grid over the catalog's `week` (each session one cell at its day and hours, merged over longer classes, in its course's page colour, "11 or 12" for merged options); sheet 2 is one row per session (course, name, component, group, day, start, end, room, instructor); sheet 3 is every rule met or broken with its cost, the register notes and what differs from #1.
  - Property tests over `problems()`: the grid holds exactly the schedule's sessions (nothing missing, nothing extra), and the JSON loads back to an equal schedule, rules and catalog.
  - `dom.js`'s `HUES` must equal `export.HUES`; `check_schedule` refuses unknown courses, components, groups and changed sessions.
- `test_cli_export.py`: `--export` / `--export-rank`, every refusal before solving (exit 2, nothing written), fewer schedules than the rank or none (results printed, nothing written, exit 1), a file that can't be written (exit 2, results kept).
- The end-to-end files export every rank of real requests through the API and the CLI and read the JSON back through the invariant checker.

## Manual checklist (report in the summary, don't automate)
- First run with nothing set up: the notice in the header says setup is needed beyond basic phrases; "Continue offline" saves it and the notice goes; Setup reopens the dialog (key field empty, "leave empty to keep the saved key").
- Once an LLM is set up, the LLM | Offline switch appears; Offline reads with the rules only and the note points back to the switch.
- Parser label and "Read by:" (`rules`, `llm`, `cache`, `rules (offline)`, `rules (LLM unavailable: …)`).
- Results stay hidden until the first solve; tabs say "every rule met" / "N preferences missed", never a cost number.
- Course search filters by id, name, instructor while typing; clicking a course expands its groups; hovering shows a bubble after a pause.
- Required / Pool / Off and the pick-N stepper.
- Review on → the button reads "Process constraints" and shows chips (Hard/Soft hover explanations, compact can't be hard), then reads "Find a schedule" and solves; editing the text turns it back to "Process constraints"; off → straight to results.
- What wasn't understood is listed under the text box, with its advice.
- The week grid has a border around it; grid hover highlights the course and dims the others; the session list is collapsed by default.
- Light and dark themes (violet / teal); reload keeps the tab's session; a new tab starts from the latest; Start over clears it.
- Merged options shown as "lab 11 or 12"; tabs show differences from #1.
- With any schedule open (not only #1), the export bar (Excel, JSON, Print / PDF) exports that schedule; the Excel file opens with the grid in the page's colours. Print preview (PDF) shows only that schedule: the grid on a landscape page 1, then the session list, rules and notes running on together (no page break between them) on portrait pages with space at the top; the "Courses:" line shows full names with the ID in parentheses, the pool on its own line, in light colours even in dark mode (Chrome/Edge; other browsers may print every page in one orientation).
