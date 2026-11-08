# Class Scheduler — Plan

## Context
A new project in the empty `scheduler/` folder. The user provides (1) a JSON file of courses and their section times, and (2) constraints and preferences in plain language, such as "no classes before 10 on Mondays", "I want Fridays off" or "keep lunch free". The app turns the plain-language input into structured constraints. The rule-based parser always runs first, and a configurable LLM (Gemini Flash-Lite recommended) is only called for text the rules can't fully understand. No model or key ships with the app: the user sets one up on first run, or chooses a more limited offline mode that uses the rules alone. It then finds the best schedules that avoid clashes and shows them in a local web UI.

Stack: Python, FastAPI backend, a lightweight HTML/JS frontend (no build step), OR-Tools CP-SAT solver, Pydantic models.

## Architecture
```
plain text ──► PreferenceParser ──► Constraint list (typed, validated) ──► user reviews/edits in UI
                 ├─ LLMParser (provider from .env)                               │
                 └─ RuleParser (regex/keywords, offline)                         ▼
courses.json ──► Catalog loader (Pydantic) ─────────────────────────────► Solver (CP-SAT) ──► top-K schedules ──► calendar view
```
The key design point is that the LLM's only job is to produce a list of typed constraints. Solving is deterministic and never depends on the LLM. Both parsers produce the same output format.

**Rules first, LLM only when needed.** Every parse tries the RuleParser on the whole text first. If it understands all of it, the LLM is not called. The LLM gets the whole text only when something is left unparsed and an LLM is set up. This keeps calls (and free-tier limits) for the requests that need them. See Parsing → Router.

**First-run setup; offline works without it.** No model or API key ships with the app. On first run (no LLM configured and no earlier choice saved), the web UI shows a setup panel and the CLI offers `python -m scheduler setup`: pick a provider (Gemini recommended), paste a key, and it is written to `.env`. Choosing **Continue offline** saves `LLM_PROVIDER=none` so the prompt doesn't come back, and the user is told plainly what that means: only simple, direct phrasing is understood ("no classes before 10", "Fridays off"), and anything else is listed as not understood. While offline, the parser label says "rules (offline)" and the unparsed list repeats that setting up an LLM would let free-form text through. Setup can be reopened at any time.

**The review step is optional.** It's a "Review constraints before solving" checkbox in the UI, or the `--review` flag in the CLI. When it's on, the parsed constraints are shown as editable chips and solving waits for you to confirm. When it's off, the app parses and solves in one step, but the constraints it used are still shown next to the results. In both modes, anything the parser didn't understand is always shown (see Parsing).

**The user must always be fully informed about a schedule they're shown.** This is a core principle, so every interface has to carry it through. With each result, show every rule, hard and soft, and whether it was met or broken. When there's a trade-off, say what it is. Don't hide a choice the user could reasonably care about, and when nothing fits, name the rules that conflict. If they're unhappy with what they see, they can change their rules and try again.

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
      base.py               # PreferenceParser protocol, ParseResult (constraints + unparsed fragments + spans)
      rules.py              # rule parser: always runs first; the only parser in offline mode
      llm.py                # LLMParser: prompt + JSON-schema output + validation/repair
      providers/            # gemini.py (default), anthropic.py, openai.py, ollama.py — each a small adapter
      router.py             # whole-text cache → rules first → LLM only if needed and configured (budget, errors → rules result)
    config.py               # reads .env; writes it from first-run setup
    cli.py                  # python -m scheduler courses.json "prefs..." [--review] [--pick 5:CS101,CS102,...] [--no-required] [--offline] [--top 5] [--export out.xlsx]
                            # python -m scheduler setup   (choose provider + key, or offline)
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
      "id": "CS101", "name": "Intro to CS", "credits": 4, "required": true,
      "components": {
        "lecture": [
          {"group": "1", "instructor": "Cohen", "sessions": [{"day": "Sun", "start": "10:00", "end": "12:00", "room": "B-201"}]},
          {"group": "2", "instructor": "Levi",  "attendance_required": false, "sessions": [...]}
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

Each course has an optional **`required`** field (default `false`) marking it as a degree requirement rather than an elective. It is the data-file form of `must_take`: `default_constraints(catalog)` turns each required course into a hard `must_take`, and the UI and CLI start from that list. **The solver never reads `required`**, only the `must_take` constraints, so the user can still drop a required course (picker toggle, review step, or `--no-required` in the CLI).

All session times are **on the hour** (`"10:00"`, not `"10:30"`). The loader rejects anything else.

The lab numbers follow the lecture they belong to (11 and 12 under lecture 1, 21 and 22 under lecture 2), and each lab records this in an optional `parent` field. **For now `parent` is only stored and displayed. Any lab can go with any lecture.**

> **Future option (not in v1):** a `link_groups` setting, per course or global, that only allows labs whose `parent` matches the chosen lecture (lecture 1 → labs 1X). In the solver this is one constraint: `lab[g] ⇒ lecture[g.parent]`. The `parent` field is in the format now so adding this later won't change the format.

Each group has an optional **`attendance_required`** field (default `true`), meaning whether you have to attend this class. It is unrelated to `required`/`must_take`, which are about taking the course at all. The format checks it and the UI shows a small "attendance optional" tag, but **v1 doesn't use it in solving or parsing**. Every group is still treated as clash-free. The field is there so the overlap feature below won't need a format change.

Days are written `Sun`–`Sat`. **The week defaults to Sun–Fri**, so the calendar grid and the meaning of "every day" in constraints are Sun–Fri. **The weekend is Fri–Sat** whatever the week: "weekends" means Fri and Sat, and "weekdays" means the week's days other than Fri and Sat (Sun–Thu by default). The week is set by an optional top-level `"week": ["Sun",…,"Fri"]` field.

## Constraint vocabulary (shared by both parsers)
Each constraint has `kind`, parameters, `strength: hard | soft` and a `weight` from 1 to 10.
- `must_take(course)`, `exclude_course(course)`, `take_n_of(courses, n)`. With `take_n_of`, the solver picks the best n courses out of the pool. If the pool has exactly n courses, all of them are required (the same as listing each one under `must_take`). If the pool has fewer than n courses, the app shows a clear error before solving. **A course that is in neither a `must_take` nor a `take_n_of` pool is not scheduled.**
  - **n only counts courses in that pool.** Courses selected outside it are unaffected, and credit rules still count the whole schedule. A required course that is also in the pool uses up one of its picks. Each pool is counted separately.
  - Hard means exactly n from the pool, so a hard pool that contradicts other hard rules gives no schedule, and the user corrects their request. Soft means n is the target: a schedule can miss it either way, at a cost. Each course over costs twice what each course short costs, because dropping a course is easier on a student than adding one, so "one fewer" ranks first.
  - Optional courses mean a schedule with fewer or more courses can appear as a lower-ranked result, but an empty schedule never does.
- `no_class_before(time, days?)`, `no_class_after(time, days?)`. Times can be any `HH:MM`, unlike session times. Without `days`, the rule applies every day.
- `day_off(day)`, `max_days(n)`, `max_hours_per_day(h)`. A `day_off` outside the week has no effect.
- `free_block(day?, start, end)` — for example lunch
- `max_gap(minutes)`, `compact` (minimise idle time between classes). `compact` is soft only; the hard version is `max_gap(0)`.
- `prefer_group(course, component, group)`, `avoid_group(...)`
- `prefer_instructor(name)`, `avoid_instructor(name)`. These apply per component. `prefer_instructor` only affects components where that instructor teaches a group in the catalog. `avoid_instructor` affects any group they teach.
- `min_credits(n)`, `max_credits(n)`

Wording sets the strength. "Must", "can't" and "never" become hard constraints. "Prefer", "ideally" and "if possible" become soft ones.

A constraint naming a course, component, group or instructor that isn't in the catalog is an error before solving.

**Soft penalties scale with how badly a rule is broken**, then get multiplied by the weight. All are whole numbers, rounded up:
| Rule | Penalty |
|---|---|
| `must_take`, `exclude_course`, `day_off` | 1 if broken |
| `take_n_of` | 1 per course short of n, 2 per course over n |
| `no_class_before`, `no_class_after`, `free_block` | hours of class in the forbidden time |
| `max_days` | days over the limit |
| `max_hours_per_day` | hours over the limit, summed across days |
| `max_gap` | hours over the limit, summed across gaps |
| `compact` | idle hours between classes |
| `prefer_group`, `avoid_group`, `prefer_instructor`, `avoid_instructor` | 1 per chosen component that breaks it |
| `min_credits`, `max_credits` | credits short or over |

## Parsing
- **LLMParser**: the system prompt includes the constraint JSON schema plus the catalog's course IDs, names and instructors, so the model can resolve phrases like "the algorithms course" or "Prof. Levi". The parser asks for JSON output that follows the schema (the native structured-output mode of Gemini, OpenAI or Anthropic tool use, or Ollama's `format`). Output is validated with Pydantic; if validation fails, the parser retries once and includes the error. Anything the model couldn't map goes in `unparsed` and is shown to the user.
- **RuleParser** (deliberately simple): splits the text into clauses on `.`, `,`, `;` and "and", then matches each clause against a small set of patterns for times ("before 10", "after 4pm"), days ("fridays", "sun"), keywords ("off", "lunch", "gap", "compact", "avoid", "prefer") and fuzzy matches against course and instructor names (`difflib`). It also uses the hard/soft keyword rules. It doesn't guess: any clause it can't fully match is returned as-is in `unparsed`. Details:
  - **Splitting**: time ranges ("12-13", "12 to 1", "between 12 and 1") and lists of days or names ("Mon, Wed and Fri off", "prefer Cohen and Adler") are recognised before splitting, so they stay in one clause and the rule applies to each item. A `.` between digits ("4.30pm") or after a title ("Prof.", "Dr.") doesn't split. Clauses made only of filler ("please", "thanks") are dropped silently.
  - **Times**: am/pm and 24-hour times are taken as written, and "noon" is 12:00. A bare hour 1–7 means pm and 8–12 is taken as given. In a range, a bare start takes the end's am/pm when that keeps it before the end ("8-10pm" → 20–22, "11-1pm" → 11–13), and a bare start isn't moved to pm if that would put it after the end ("7-9" → 07–09). The chips and breakdown always show the time used. Impossible times or ranges ("before 25", "13-12") are unparsed.
  - **Days**: day names and short forms, "every day", "weekdays", "weekends" (Fri–Sat, see Catalog format), and "except \<days\>" (the week minus those days). Vague periods such as "mornings" or "evenings" stay unparsed.
  - **Strength**: "must", "can't", "never", "need" → hard; "prefer", "ideally", "if possible", "avoid", "rather" → soft; **no keyword → hard**. Both kinds in one clause → unparsed. Weight is always the default 5.
  - **Kinds covered**: every kind except `take_n_of` (the picker's pick-N box covers it). "no gaps" → `max_gap(0)`, with strength from the wording; "compact" or "minimise gaps" → `compact`, and a hard "must be compact" → hard `max_gap(0)`. "lunch" is 12:00–13:00 unless times are given. "avoid X" is soft; "never X" and "no X" are hard. On a course, "prefer X" is a soft `must_take` and "avoid X" a soft `exclude_course`; "take", "drop", "skip" and "don't take" only apply to courses. "want" is left to the LLM (it's filler in "I want Fridays off").
  - **Groups**: named by component ("CS101 lab 11", "lab 11 of CS101"), by "group", "section" or "sec" ("CS101 group 11"), or both ("lab group 11"). "lec", "tut" and "recitation" count only when the catalog has a lecture or tutorial component. Without a component, the group number must belong to just one of the course's components; otherwise it's ambiguous ("lab 11 or tutorial 11?"). "prefer" → soft `prefer_group`, "avoid" → soft `avoid_group`, "never"/"no" → hard `avoid_group`, and "take CS101 group 11" → `must_take` CS101 plus a hard `prefer_group` (take the course, and only in that group). An unknown group lists the groups there are.
  - **Names**: an exact match (case-insensitive) on a course ID, course name or instructor wins. Otherwise a course name whose words contain all the query's words, or a `difflib` match above a high cutoff that clearly beats the runner-up. Several equally good candidates → unparsed as ambiguous, listing them. No match → unparsed, with close-but-rejected names as suggestions. The rule parser only outputs names that exist in the catalog. Roman numerals II–X and digits are the same in names ("navigation 2" finds "…Principles II"); "I" isn't, because the user's "I" is the pronoun. A comma inside a list works like "and" ("take XBI, APA and NAV2"). A word the catalog doesn't use as a component is only read as one if it's a kind of class (lecture, lab, tutorial, seminar, workshop…), so "take XBI and APA" stays a list of courses.
  - The same constraint written twice is kept once.
  - Known limit: two rules joined by a day list ("no classes before 10 on mondays and fridays off") stay in one clause, so they're unparsed rather than guessed at. Splitting them is a job for the LLM, or the user separates them with a full stop or semicolon.
- **`ParseResult`**: `constraints`; `unparsed: [{text, reason, candidates}]`, where `text` is the original fragment, `reason` is one of `not_understood`, `unknown_name`, `ambiguous_name`, `invalid_time` or `conflicting_strength`, and `candidates` lists suggested names (may be empty); and `spans: [{text, constraints}]`, one per fully parsed clause in text order, so the UI can show which words produced each constraint. Spans are not part of the `Constraint` models.
- **Unparsed feedback (both parsers)**: each `ParseResult.unparsed` entry keeps the original text fragment and says why it wasn't used, so the user knows whether to rephrase, fix a name ("did you mean Levi?") or pick between candidates. The UI highlights them under the text box ("Couldn't understand: *'I'm not a morning person'*. Try rephrasing, e.g. 'no classes before 10'") so you can edit the request and parse again. The CLI prints them as warnings; with `--review` it lets you edit the text and parse again.
- **Router** (rules first, always):
  1. **Whole-text cache**, keyed by a hash of the text and catalog, so re-solving, changing K, toggling courses or going back to earlier text costs nothing.
  2. **RuleParser on the whole text.** If nothing is left unparsed, that is the result and no LLM call is made.
  3. Otherwise, **if an LLM is set up** and the daily limit isn't reached, the **whole** text goes to the LLM (never just the leftover clauses, because the meaning can depend on the rest of the text, and a partial call would cost nearly as much because the fixed prompt dominates).
  4. If no LLM is set up (offline), the limit is reached, or the call fails, the rules result is used, its unparsed fragments are shown, and the user is told why the LLM wasn't used.
  - The result records which path was used (`cache`, `rules`, `llm`, or `rules (offline)` / `rules (LLM unavailable: <reason>)`) and the UI and CLI show it.
  - Editing constraints through the review chips never re-parses, so deleting or reweighting a constraint costs nothing.
  - **Considered and dropped:** a phrase-level cache that reused cached parts of earlier LLM answers. It only saved a call when an edit was entirely known clauses, mostly deletions, which the chips already handle for free. It needed verbatim source spans from the LLM (more tokens, more failed validations, each one costing a retry call) and could silently misread meaning that spans clauses. If call budgets turn out to be tight in real use, count parses by path locally and revisit with real numbers.

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
Provider SDKs are optional extras and are imported only when that provider is selected. With no `.env`, or `LLM_PROVIDER=none`, the app runs offline with the rule parser alone; first-run setup writes this file.

## Solver (OR-Tools CP-SAT)
- One boolean per (course, component, group), plus one boolean per course meaning "taking this course". Selected courses must pick exactly one group per component.
- No overlap: for each pair of groups whose sessions overlap in time, at most one can be chosen. These pairs are computed ahead of time, which is cheap at university scale.
- Hard constraints: groups that break them are forced to 0. Day-based rules use `day_used[d]` booleans; credit limits are linear sums.
- Soft constraints: penalty terms multiplied by weight, then minimised. Gaps and compactness are measured with per-day first/last-class times (`span − class minutes`).
- **Top-K results, never repeated.** Default K=5, changeable in the UI (number box next to Schedule, 1–20) or with `--top N` in the CLI. After each solution, add a constraint that rules out that exact group combination, then solve again. Stop at K solutions **or as soon as the model has no more solutions**, whichever comes first. A simple catalog with only 2 valid schedules therefore returns exactly 2.
  - **Groups that meet at exactly the same times are merged before solving.** Within each course component, groups with identical sessions (same days, start and end times) become one option, shown as "lab 11 or 12" with the rooms and instructors of each. So two results always look different on the week grid. Soft preferences such as `prefer_instructor` or `prefer_group` still apply: a merged option is scored by its best member, and the result tells you which member to register for. Hard group and instructor rules remove individual members before merging, so an option can shrink to the members that are left. If members tie on cost but break different preferences, the result stays a single one and says so: "Register for 11, or 12, which breaks X instead of Y".
  - Two schedules count as different when any course or merged option differs. Each extra tab shows how it differs from #1, for example "CS101 lab 21 or 22 instead of 11 or 12".
  - If fewer than K exist, the UI and CLI show "Only 2 possible schedules match your constraints" instead of padding the list.
  - All results must also satisfy every hard constraint. Lower-ranked results are only worse on soft preferences, and their score breakdown shows which ones.
- If there is no solution: drop hard constraints one at a time to find a minimal set that conflicts, and report them, for example: "Fri off + must take CS201 conflict". The message names the constraints but doesn't explain why they conflict. If several separate conflicts exist, one is reported.
  - While searching, the full request's courses stay available: dropping a `must_take` or hard pool drops the requirement, not the course. Otherwise dropping one could make the request *harder* (only excluded courses left) and the wrong rule would be blamed.
  - "A schedule has at least one course" is treated as a rule the search can drop. It's tried first, so it's only blamed when needed, and then the result sets `nothing_to_schedule` and says so: "exclude A + exclude B leave no course that can be scheduled". Course requests are tried last, so when there are separate conflicts, the one naming the courses the user asked for is preferred.
- Each result includes a breakdown of **every** constraint, hard and soft, showing whether it was met or broken and what breaking it cost.
- **Time limit: 3 minutes per request** (all top-K solves plus any conflict search). Realistic requests (up to ~10 courses, small K) take seconds. If the limit is hit, results already found are kept (each was proven best in turn), `timed_out` is set, and the note says what's unknown: "Stopped after 3 minutes: showing the 2 found; there may be more", or "…without finishing. Try fewer courses or a smaller number of results", or that the conflicting rules couldn't be found in time. Nothing unproven is claimed (never "no schedule works" after a timeout).

## Web UI
A single page:
1. Upload or select a courses JSON file.
2. **Course picker** with a search box that filters as you type by course ID, name and instructor (filtered in the browser). Each course has a three-way toggle: *required* / *in pool* / *off*, defaulting from the course's `required` field. Under the pool there's a "pick **N** of these" number box (default: the pool size, so picking 5 when 5 are pooled takes all 5).
3. Preferences text box, plus a ☐ **Review constraints before solving** checkbox and a **Schedule** button.
   - Checkbox on: shows editable constraint chips (toggle hard/soft, change the weight, delete) and an "add constraint" form, then a **Solve** button.
   - Checkbox off: goes straight to results.
   - Unparsed fragments are always highlighted under the text box.
4. Results: tabs for the top-K schedules, each with a weekly time grid (one colour per course), the full breakdown of every rule met or broken, any "Register for 11, or 12, which breaks X instead of Y" notes, and export buttons:
   - **PDF**: the browser's own print-to-PDF (`window.print()`) with a print stylesheet that hides everything except the selected schedule's grid and its course list. No extra dependencies.
   - **Excel**: `GET /api/export/xlsx` builds the file with openpyxl. Sheet 1 is the weekly grid with coloured cells; sheet 2 lists each course, component, group, day, time, room and instructor. The CLI has `--export out.xlsx`.
   - **JSON**: the raw solution, for debugging or reloading.

## Build order
1. `models.py` and `catalog.py` with sample data (validation, whole hours, merging), then the solver with no-overlap and `must_take` only, returning a top-K list from the start (never repeated, "only N possible" note; unranked until step 2), plus tests.
2. All other constraint kinds in the solver (hard and soft), scoring and ranking, and conflict explanation.
3. The rule parser and its tests (a set of test phrases with expected constraints).
4. Provider adapters, the LLM parser, the router (whole-text cache → rules first → LLM only if needed; budget; fallback with the reason shown), and config reading/writing `.env` for setup.
   - **Before starting, discuss how to keep each call as cheap as possible.** The app should work for as many people as possible, including those on free tiers or with tight limits. Also decide whether the LLM should return source spans (useful for showing which words produced each chip, but more output tokens and more failed validations).
5. The CLI (`--review`, `--pick`, `--no-required`, `--offline`), plus `python -m scheduler setup` and the first-run offer and offline notice.
6. The FastAPI endpoints and the frontend (first-run setup panel with "Continue offline", search, pick-N, review toggle, unparsed highlighting, parser-path label).
   - **A loading indicator while solving.** A request can take up to the 3-minute limit: a reasonable wait, but a long time for a frozen screen. Show that it's working, and show the `timed_out` note clearly when it applies.
7. Exports: print stylesheet (PDF), openpyxl Excel export, JSON.

## Deferred / out of scope
- `link_groups`: restricting labs to the matching lecture (format already supports it via `parent`).
- **Allowed overlaps for optional-attendance classes**: a setting like "allow up to X overlaps" where a class with `attendance_required: false` may clash with another class. Likely solver approach: only add the no-overlap constraint for a clashing pair if both groups have `attendance_required: true`. For pairs with an optional group, add an `overlap[p]` boolean with `sum(overlap) ≤ X`, and a soft penalty so overlaps are only used when needed. It would also need a constraint kind (`max_overlaps(n)`), parser phrases ("I don't mind missing some lectures"), and overlap highlighting on the grid.
- Travel time between rooms and online vs in-person: intentionally ignored.

## Verification
- `pytest`: catalog validation; the solver on small hand-made catalogs (known best answer, infeasible cases explained, top-K results all different; a catalog with only 2 valid schedules and K=5 returns exactly 2 with the "only 2 possible" note; back-to-back 10–12 then 12–14 is allowed; a group with several sessions is all-or-nothing, so a clash with only its second session rules out the whole group; pick-N where the pool equals N takes all courses; any lab is allowed with any lecture; two labs at identical times merge into one option and don't produce duplicate-looking results; `attendance_required: false` is accepted but doesn't change the solution; times not on the hour are rejected); each RuleParser phrase test; the LLM parser using a mocked provider (valid output, invalid output followed by retry, and fallback on error); the router never calls the LLM when the rules parse everything, calls it with the whole text when something is unparsed, and runs offline with no `.env` at all.
- Manual: `uvicorn scheduler.web.app:app --reload`, load `data/sample_courses.json`, enter "no classes before 10, prefer Cohen, keep 12-13 free" (adding "fridays off" should report the ZGC conflict; see `data/README.md`), parse once with `LLM_PROVIDER=gemini` and once with `none`, and check that the chips and schedules make sense.
