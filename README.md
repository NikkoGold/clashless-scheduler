# Class Scheduler

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
python -m scheduler data/sample_courses.json "no classes before 10, fridays off" --top 5
```

CLI options: `--review`, `--pick N:CS101,CS102,...`, `--parser rules|llm`, `--top N`, `--export out.xlsx`.

## Tests

```sh
pytest
```
