---
name: test-interfaces
description: Test-first workflow for the scheduler's user-facing layers — the CLI (cli.py), FastAPI endpoints (web/app.py) and exports (Excel via openpyxl, JSON, PDF print stylesheet). Use when starting build steps 5, 6 or 7, or changing CLI flags, API routes or export output. Frontend and PDF are covered by a manual checklist.
---

# CLI, API and export tests (build steps 5–7)

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). Build input catalogs with the catalog builder and write them to `tmp_path` when a file is needed. Force `--offline` / `LLM_PROVIDER=none` so nothing hits the network.

## CLI (`test_cli.py`)
Call `main(argv)` directly and capture output with `capsys`; assert on exit code and text.
- Basic run prints K schedules; `--top N` changes the count.
- "Only N possible" message when fewer than K exist.
- `--pick 2:CS101,CS102,CS103` → exactly 2 of the pool chosen; pool < N → clear error, non-zero exit.
- Courses with `"required": true` are scheduled by default (as `must_take`); `--no-required` drops them.
- `--offline` never calls the LLM even when one is configured; the parser path used is always printed.
- No `.env`: runs offline, prints the one-line "offline mode is limited; run `python -m scheduler setup`" notice, and still solves.
- `setup`: choosing a provider writes `.env` (to `tmp_path`); "continue offline" writes `LLM_PROVIDER=none` and the first-run notice stops appearing.
- Unparsed fragments are printed as warnings.
- Infeasible input prints the conflict explanation, non-zero exit.
- `--export out.xlsx` writes the file (checked as below).
- `--review`: feed edited text via monkeypatched `input`, confirm re-parse happens.

## API (`test_api.py`)
Use FastAPI `TestClient`.
- `/api/catalog`: valid upload returns courses; invalid JSON / schema error → 4xx with a readable message.
- `/api/parse`: returns constraints, `unparsed`, and `parser`.
- `/api/solve`: top-K results with breakdown and diff-vs-#1; `top` param bounded 1–20; infeasible → explanation, not a 500.
- `/api/export/xlsx`: correct content type and a loadable workbook.
- Static `index.html` is served.

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
