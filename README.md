# Clashless: A Class Schedule.
## Plain-language preferences to a clean weekly schedule.

Give it a JSON file of courses and section times, and describe your preferences in plain language ("no classes before 10 on Mondays", "Fridays off", "keep lunch free"). It returns the best clash-free weekly schedules.

- Preferences are turned into typed constraints by a built-in rule parser first. An LLM of your choice (Gemini Flash-Lite is recommended as a free basic tier) is only called when the rules can't understand your text.
- Schedules are solved deterministically with OR-Tools CP-SAT. The LLM never affects solving.
- A local web UI shows the top results on a weekly grid. The export bar above the grid saves whichever schedule is open to Excel, JSON or PDF (print, then save as PDF).

See [plan.md](plan.md) for the full design.

## Setup

Requires Python 3.11+.

```sh
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,gemini]"  # or anthropic / openai; Ollama and rules-only need no extra
python -m scheduler setup       # choose a provider and paste your key, or continue offline
```

No model or key comes with the app. The CLI offers setup on first run, and the web page shows a Setup button until you choose. You can run setup again at any time. Providers are Gemini, Anthropic, OpenAI, or a local Ollama (no key; default `http://localhost:11434`).

**Offline mode** needs no setup and works fully, but it only understands simple, direct phrasing ("no classes before 10", "Fridays off"). Anything else is listed as not understood so you can rephrase it. Once an LLM is set up, the page has an LLM | Offline switch for when you want the rules only.

Setup saves your choice to a user-wide file (`%LOCALAPPDATA%\scheduler\config.env` on Windows, `~/.config/scheduler/config.env` elsewhere), so it works from any folder. The key is stored in plain text there. A `.env` in the folder you run from overrides that file entirely (see [.env.example](.env.example)), and the CLI always prints which file it used.

## Usage

```sh
# Web page (opens your browser; served on this computer only)
python -m scheduler web          # --port N (default 8000, or the next free one), --no-browser

# CLI
python -m scheduler data/sample_courses.json "no classes before 10, keep 12-13 free" --top 5
```

### Try it with a dummy dataset
[data/sample_courses.json](data/sample_courses.json) is a default dataset to experiment with: 16 made-up courses (8 required, 8 electives by default) that show off clashes, merged labs, back-to-back classes and an unsolvable case. [data/README.md](data/README.md) explains what each course demonstrates and suggests phrases to try.

### CLI options
- `--pick 2:CS101,CS102,CS103` chooses 2 courses from a pool (`--pick CS101,CS102` takes all of them).
- `--drop ZGC` switches off a required course, and `--no-required` switches off all of them.
- `--review` shows the parsed rules as a numbered list to edit before solving (`d 2 4` delete, `h 1` hard/soft, `w 1 3 7` weight, `t` new text, `+ phrase` add, `?` help, Enter solves).
- `--verbose` lists every rule in each result, met or broken.
- `--offline` uses the rule parser only for this run, and `--top N` sets how many schedules to show (1-20, default 5).
- Give `-` instead of the preferences text to read it from stdin.
- `--export out.xlsx` (or `out.json`) writes schedule 1 to a file, and `--export-rank 3` picks another one.

Exit codes: 0 schedules found, 1 no solution (the conflicting rules are named), 2 a usage or input error.

## Courses file

```json
{
  "term": "2026A",
  "courses": [
    {
      "id": "CS101", "name": "Intro to CS", "credits": 4, "required": true,
      "components": {
        "lecture": [
          {"group": "1", "instructor": "Cohen", "sessions": [
            {"day": "Mon", "start": "10:00", "end": "12:00", "room": "B-201"},
            {"day": "Wed", "start": "10:00", "end": "12:00", "room": "B-201"}
          ]}
        ],
        "lab": [
          {"group": "11", "parent": "1", "sessions": [{"day": "Tue", "start": "14:00", "end": "16:00"}]},
          {"group": "12", "parent": "1", "sessions": [{"day": "Thu", "start": "14:00", "end": "16:00"}]}
        ]
      }
    }
  ]
}
```

### How a schedule is built
- **One group from each component.** For each course you take, you get exactly one lecture group, one lab group, and so on. Component names are free text (`lecture`, `tutorial`, `lab`, ...).
- **A group's sessions come as a package.** If lecture group 1 meets Mon *and* Wed, choosing it means attending both. A clash with any one of its sessions rules out the whole group.
- **Back-to-back classes are fine.** 10:00–12:00 then 12:00–14:00 is not a clash. Travel time between rooms and online vs in-person are not considered.
- **Any lab can go with any lecture.** `parent` (lab 11 belongs to lecture 1) is shown but not enforced yet.
- **Groups that meet at exactly the same times count as one option**, shown as "lab 11 or 12", so no two results look the same on the grid. Register for whichever you like.
- **Only the courses you select are scheduled.** Courses marked `"required": true` are selected by default (switch them off in the picker or with `--no-required`). Electives are only scheduled if you add them, for example as a "pick N of these" pool.
- **If fewer schedules exist than you asked for**, you get all of them and a note such as "Only 2 possible schedules match your constraints".

### File rules
The file is checked when it's loaded, and errors name the course, group and session at fault.
- **Times are whole hours**, written `"HH:00"`. `"10:30"` is rejected.
- **Days** are `Sun`–`Sat`. The week is Sun–Fri unless the file sets one at the top level, e.g. `"week": ["Mon", "Tue", "Wed", "Thu", "Fri"]`; a session on a day outside the week is an error.
- `end` must be after `start`, and a group's own sessions can't overlap each other.
- Course IDs are unique, and group IDs are unique within a component (a tutorial 11 and a lab 11 is fine).
- Unknown fields are errors, so typos like `"instrutor"` are caught.
- `credits` may be fractional (`1.5`).
- `attendance_required` (default `true`) marks a class you may skip. It is stored but **not used yet**; every class is still kept clash-free.

## Tests

```sh
pytest
```

The test suite runs fully offline, with no key or settings needed. A separate live eval sends real phrases to your configured LLM. It's opt-in and uses up to 20 calls of your quota: `RUN_LIVE_LLM=1 pytest -m live -s`.
