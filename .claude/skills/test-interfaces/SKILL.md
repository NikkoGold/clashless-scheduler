---
name: test-interfaces
description: Test-first workflow for the scheduler's user-facing layers — the CLI (cli.py), FastAPI endpoints (web/app.py) and exports (Excel via openpyxl, JSON, PDF print stylesheet). Use when starting build steps 5, 6 or 7, or changing CLI flags, API routes or export output. Frontend and PDF are covered by a manual checklist.
---

# CLI, API and export tests (build steps 5–7)

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). Build input catalogs with the catalog builder and write them to `tmp_path` when a file is needed. Force `--parser rules` / `LLM_ENABLED=false` so nothing hits the network.

## CLI (`test_cli.py`)
Call `main(argv)` directly and capture output with `capsys`; assert on exit code and text.
- Basic run prints K schedules; `--top N` changes the count.
- "Only N possible" message when fewer than K exist.
- `--pick 2:CS101,CS102,CS103` → exactly 2 of the pool chosen; pool < N → clear error, non-zero exit.
- `--parser rules|llm` is honoured and the parser used is printed.
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
- Course search filters by id, name, instructor while typing.
- required / in pool / off toggle and "pick N" box.
- Review checkbox on → editable chips + Solve; off → straight to results.
- Unparsed fragments highlighted under the text box.
- Merged options shown as "lab 11 or 12"; tabs show differences from #1.
- Print preview (PDF) shows only the selected grid and course list.
