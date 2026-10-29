# Class Scheduler — Plan

## Context
A new project in the empty `scheduler/` folder. The user provides (1) a JSON file of courses and their section times, and (2) constraints and preferences in plain language, such as "no classes before 10 on Mondays", "I want Fridays off" or "keep lunch free". The app turns the plain-language input into structured constraints, using a configurable LLM (Gemini Flash-Lite by default) or a rule-based fallback parser for offline use or to save money. It then finds the best schedules that avoid clashes and shows them in a local web UI.

Stack: Python, FastAPI backend, a lightweight HTML/JS frontend (no build step), OR-Tools CP-SAT solver, Pydantic models.

## Architecture
```
plain text ──► PreferenceParser ──► Constraint list (typed, validated) ──► user reviews/edits in UI
                 ├─ LLMParser (provider from .env)                               │
                 └─ RuleParser (regex/keywords, offline)                         ▼
courses.json ──► Catalog loader (Pydantic) ─────────────────────────────► Solver (CP-SAT) ──► top-K schedules ──► calendar view
```
The key design point is that the LLM's only job is to produce a list of typed constraints. Solving is deterministic and never depends on the LLM. Both parsers produce the same output format.

**The review step is optional.** It's a "Review constraints before solving" checkbox in the UI, or the `--review` flag in the CLI. When it's on, the parsed constraints are shown as editable chips and solving waits for you to confirm. When it's off, the app parses and solves in one step, but the constraints it used are still shown next to the results. In both modes, anything the parser didn't understand is always shown (see Parsing).

**Back-to-back classes are allowed.** There is no travel time and no online/in-person distinction. A class ending at 12:00 and another starting at 12:00 do not clash, because the overlap test is `a.start < b.end and b.start < a.end`.

## Project layout
```
scheduler/
  pyproject.toml            # fastapi, uvicorn, pydantic, ortools, python-dotenv, httpx, openpyxl; extras: gemini, anthropic, openai
                            # dev extras: pytest; pyyaml (loads tests/data/phrases.yaml, the shared parser phrase cases);
                            #   hypothesis (random small-catalog property tests checked against the brute-force oracle, added after build step 2)
  .env.example              # LLM_PROVIDER, LLM_MODEL, API keys, LLM_ENABLED, LLM_MAX_CALLS_PER_DAY
  data/sample_courses.json
  scheduler/
    models.py               # Catalog + Constraint schemas (Pydantic)
    catalog.py              # load/validate courses JSON, time helpers
    constraints.py          # constraint types + how each one maps into the solver
    solver.py               # CP-SAT model, top-K enumeration, scoring breakdown
    parsing/
      base.py               # PreferenceParser protocol, ParseResult (constraints + unparsed fragments)
      rules.py              # offline fallback parser
      llm.py                # LLMParser: prompt + JSON-schema output + validation/repair
      providers/            # gemini.py (default), anthropic.py, openai.py, ollama.py — each a small adapter
      router.py             # choose LLM or rules from config, budget and errors; caching
    config.py               # reads .env
    cli.py                  # python -m scheduler courses.json "prefs..." [--review] [--pick 5:CS101,CS102,...] [--parser rules|llm] [--top 5] [--export out.xlsx]
    web/
      app.py                # FastAPI: /api/catalog, /api/parse, /api/solve; serves static files
      static/index.html, app.js, style.css   # upload, preference box, constraint chips, weekly grid
  tests/
```

## Courses JSON format (proposed)
```json
{
  "term": "2026A",
  "courses": [
    {
      "id": "CS101", "name": "Intro to CS", "credits": 4,
      "components": {
        "lecture": [
          {"group": "1", "instructor": "Cohen", "sessions": [{"day": "Sun", "start": "10:00", "end": "12:00", "room": "B-201"}]},
          {"group": "2", "instructor": "Levi",  "mandatory": false, "sessions": [...]}
        ],
        "lab": [
          {"group": "11", "parent": "1", "sessions": [...]},
          {"group": "12", "parent": "1", "sessions": [...]},
          {"group": "21", "parent": "2", "sessions": [...]},
          {"group": "22", "parent": "2", "sessions": [...]}
        ]
      }
    }
  ]
}
```
Rule: a valid schedule picks exactly one group from each component of each selected course. Component names are free text (for example lecture, tutorial or lab).

The lab numbers follow the lecture they belong to (11 and 12 under lecture 1, 21 and 22 under lecture 2), and each lab records this in an optional `parent` field. **For now `parent` is only stored and displayed. Any lab can go with any lecture.**

> **Future option (not in v1):** a `link_groups` setting, per course or global, that only allows labs whose `parent` matches the chosen lecture (lecture 1 → labs 1X). In the solver this is one constraint: `lab[g] ⇒ lecture[g.parent]`. The `parent` field is in the format now so adding this later won't change the format.

Each group has an optional **`mandatory`** field (default `true`), meaning whether attendance is required. The format checks it and the UI shows a small "attendance optional" tag, but **v1 doesn't use it in solving or parsing**. Every group is still treated as clash-free. The field is there so the overlap feature below won't need a format change.

Days are written `Sun`–`Sat`. **The week defaults to Sun–Fri**, so the calendar grid and the meaning of "every day" or "weekdays" in constraints are Sun–Fri. The week is set by an optional top-level `"week": ["Sun",…,"Fri"]` field.

## Constraint vocabulary (shared by both parsers)
Each constraint has `kind`, parameters, `strength: hard | soft` and a `weight` from 1 to 10.
- `must_take(course)`, `exclude_course(course)`, `take_n_of(courses, n)`. With `take_n_of`, the solver picks the best n courses out of the pool. If the pool has exactly n courses, all of them are required (the same as listing each one under `must_take`). If the pool has fewer than n courses, the app shows a clear error before solving.
- `no_class_before(time, days?)`, `no_class_after(time, days?)`
- `day_off(day)`, `max_days(n)`, `max_hours_per_day(h)`
- `free_block(day?, start, end)` — for example lunch
- `max_gap(minutes)`, `compact` (minimise idle time between classes)
- `prefer_group(course, component, group)`, `avoid_group(...)`
- `prefer_instructor(name)`, `avoid_instructor(name)`
- `min_credits(n)`, `max_credits(n)`

Wording sets the strength. "Must", "can't" and "never" become hard constraints. "Prefer", "ideally" and "if possible" become soft ones.

## Parsing
- **LLMParser**: the system prompt includes the constraint JSON schema plus the catalog's course IDs, names and instructors, so the model can resolve phrases like "the algorithms course" or "Prof. Levi". The parser asks for JSON output that follows the schema (the native structured-output mode of Gemini, OpenAI or Anthropic tool use, or Ollama's `format`). Output is validated with Pydantic; if validation fails, the parser retries once and includes the error. Anything the model couldn't map goes in `unparsed` and is shown to the user.
- **RuleParser** (deliberately simple): splits the text into clauses on `.`, `,`, `;` and "and", then matches each clause against a small set of patterns for times ("before 10", "after 4pm"), days ("fridays", "sun"), keywords ("off", "lunch", "gap", "compact", "avoid", "prefer") and fuzzy matches against course and instructor names (`difflib`). It also uses the hard/soft keyword rules. It doesn't guess: any clause it can't fully match is returned as-is in `unparsed`.
- **Unparsed feedback (both parsers)**: `ParseResult.unparsed` is a list of the original text fragments. The UI highlights them under the text box ("Couldn't understand: *'I'm not a morning person'*. Try rephrasing, e.g. 'no classes before 10'") so you can edit the request and parse again. The CLI prints them as warnings; with `--review` it lets you edit the text and parse again.
- **Router**: uses the rule parser when `LLM_ENABLED=false`, when no key is set, when a network or API error occurs, or when the daily call limit is reached. It caches results by a hash of the text and catalog, so re-solving costs nothing. The UI shows which parser was used.

## Config (.env)
```
LLM_PROVIDER=gemini        # gemini | anthropic | openai | ollama | none
LLM_MODEL=gemini-flash-lite-latest
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
OLLAMA_URL=http://localhost:11434
LLM_MAX_CALLS_PER_DAY=50
```
Provider SDKs are optional extras and are imported only when that provider is selected.

## Solver (OR-Tools CP-SAT)
- One boolean per (course, component, group), plus one boolean per course meaning "taking this course". Selected courses must pick exactly one group per component.
- No overlap: for each pair of groups whose sessions overlap in time, at most one can be chosen. These pairs are computed ahead of time, which is cheap at university scale.
- Hard constraints: groups that break them are forced to 0. Day-based rules use `day_used[d]` booleans; credit limits are linear sums.
- Soft constraints: penalty terms multiplied by weight, then minimised. Gaps and compactness are measured with per-day first/last-class times (`span − class minutes`).
- **Top-K results, never repeated.** Default K=5, changeable in the UI (number box next to Schedule, 1–20) or with `--top N` in the CLI. After each solution, add a constraint that rules out that exact group combination, then solve again. Stop at K solutions **or as soon as the model has no more solutions**, whichever comes first. A simple catalog with only 2 valid schedules therefore returns exactly 2.
  - **Groups that meet at exactly the same times are merged before solving.** Within each course component, groups with identical sessions (same days, start and end times) become one option, shown as "lab 11 or 12" with the rooms and instructors of each. So two results always look different on the week grid. Soft preferences such as `prefer_instructor` or `prefer_group` still apply: a merged option is scored by its best member, and the result tells you which member to register for.
  - Two schedules count as different when any course or merged option differs. Each extra tab shows how it differs from #1, for example "CS101 lab 21 or 22 instead of 11 or 12".
  - If fewer than K exist, the UI and CLI show "Only 2 possible schedules match your constraints" instead of padding the list.
  - All results must also satisfy every hard constraint. Lower-ranked results are only worse on soft preferences, and their score breakdown shows which ones.
- If there is no solution: drop hard constraints one at a time to find which ones conflict, and report them, for example: "Fridays off + must take CS201 conflict: CS201 only meets Fridays".
- Each result includes a score breakdown showing which preferences were met or broken.

## Web UI
A single page:
1. Upload or select a courses JSON file.
2. **Course picker** with a search box that filters as you type by course ID, name and instructor (filtered in the browser). Each course has a three-way toggle: *required* / *in pool* / *off*. Under the pool there's a "pick **N** of these" number box (default: the pool size, so picking 5 when 5 are pooled takes all 5).
3. Preferences text box, plus a ☐ **Review constraints before solving** checkbox and a **Schedule** button.
   - Checkbox on: shows editable constraint chips (toggle hard/soft, change the weight, delete) and an "add constraint" form, then a **Solve** button.
   - Checkbox off: goes straight to results.
   - Unparsed fragments are always highlighted under the text box.
4. Results: tabs for the top-K schedules, each with a weekly time grid (one colour per course), the score breakdown, and export buttons:
   - **PDF**: the browser's own print-to-PDF (`window.print()`) with a print stylesheet that hides everything except the selected schedule's grid and its course list. No extra dependencies.
   - **Excel**: `GET /api/export/xlsx` builds the file with openpyxl. Sheet 1 is the weekly grid with coloured cells; sheet 2 lists each course, component, group, day, time, room and instructor. The CLI has `--export out.xlsx`.
   - **JSON**: the raw solution, for debugging or reloading.

## Build order
1. `models.py` and `catalog.py` with sample data, then the solver with hard no-overlap only, plus tests.
2. All constraint kinds in the solver (hard and soft), top-K, and conflict explanation.
3. The rule parser and its tests (a set of test phrases with expected constraints).
4. Provider adapters, the LLM parser, and the router with fallback, budget and cache.
5. The CLI (`--review`, `--pick`, `--parser`).
6. The FastAPI endpoints and the frontend (search, pick-N, review toggle, unparsed highlighting).
7. Exports: print stylesheet (PDF), openpyxl Excel export, JSON.

## Deferred / out of scope
- `link_groups`: restricting labs to the matching lecture (format already supports it via `parent`).
- **Allowed overlaps for optional-attendance classes**: a setting like "allow up to X overlaps" where a class with `mandatory: false` may clash with another class. Likely solver approach: only add the no-overlap constraint for a clashing pair if both groups are mandatory. For pairs with an optional group, add an `overlap[p]` boolean with `sum(overlap) ≤ X`, and a soft penalty so overlaps are only used when needed. It would also need a constraint kind (`max_overlaps(n)`), parser phrases ("I don't mind missing some lectures"), and overlap highlighting on the grid.
- Travel time between rooms and online vs in-person: intentionally ignored.

## Verification
- `pytest`: catalog validation; the solver on small hand-made catalogs (known best answer, infeasible cases explained, top-K results all different; a catalog with only 2 valid schedules and K=5 returns exactly 2 with the "only 2 possible" note; back-to-back 10–12 then 12–14 is allowed; pick-N where the pool equals N takes all courses; any lab is allowed with any lecture; two labs at identical times merge into one option and don't produce duplicate-looking results; `mandatory: false` is accepted but doesn't change the solution); each RuleParser phrase test; the LLM parser using a mocked provider (valid output, invalid output followed by retry, and fallback on error).
- Manual: `uvicorn scheduler.web.app:app --reload`, load `data/sample_courses.json`, enter "no classes before 10, fridays off, prefer Cohen, keep 12-13 free", parse once with `LLM_PROVIDER=gemini` and once with `none`, and check that the chips and schedules make sense.
