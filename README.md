# Clashless: A Class Schedule. Plain-language preferences to a clean weekly schedule.

Give it a JSON file of courses and section times, and describe your preferences in plain language ("no classes before 10 on Mondays", "Fridays off", "keep lunch free"). It returns the best clash-free weekly schedules.

- Preferences are turned into typed constraints by an LLM (Gemini Flash-Lite by default, configurable) or by an offline rule-based parser.
- Schedules are solved deterministically with OR-Tools CP-SAT. The LLM never affects solving.
- A local web UI shows the top results on a weekly grid, with PDF, Excel and JSON export.

See [plan.md](plan.md) for the full design.

## Setup

```sh
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,gemini]"  # or anthropic / openai; leave it out for rules-only
copy .env.example .env          # then add your API key
```

Set `LLM_ENABLED=false` or `LLM_PROVIDER=none` to run fully offline with the rule parser.

## Usage

```sh
# Web UI
uvicorn scheduler.web.app:app --reload

# CLI
python -m scheduler data/sample_courses.json "no classes before 10, keep 12-13 free" --top 5
```

CLI options: `--review`, `--pick N:CS101,CS102,...`, `--no-required`, `--parser rules|llm`, `--top N`, `--export out.xlsx`.

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

[data/sample_courses.json](data/sample_courses.json) is a full example.

### How your schedule is built
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
- **Days** are `Sun`–`Sat`. The week is Sun–Fri unless the file sets `"week"`; a session on a day outside the week is an error.
- `end` must be after `start`, and a group's own sessions can't overlap each other.
- Course IDs are unique, and group IDs are unique within a component (a tutorial 11 and a lab 11 is fine).
- Unknown fields are errors, so typos like `"instrutor"` are caught.
- `credits` may be fractional (`1.5`).
- `attendance_required` (default `true`) marks a class you may skip. It is stored but **not used yet**; every class is still kept clash-free.

## Tests

```sh
pytest
```
