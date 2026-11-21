"""Run the CLI in-process, the way the end-to-end and CLI tests do.

    run = cli(SAMPLE, "fridays off", "--offline")
    run.code, run.out, run.err      # what the user sees
    run.outcome.result              # the SolveResult behind it, for the invariant checker
    run.outcome.constraints         # every constraint it solved with (courses + parsed rules)

Settings come from the `fresh_settings` fixture (an empty user-wide folder and working folder per test).
"""
import io
import json
from dataclasses import dataclass
from pathlib import Path

from scheduler.cli import Outcome, run
from tests.helpers.fake_console import FakeConsole

ROOT = Path(__file__).parent.parent.parent
SAMPLE = ROOT / "data" / "sample_courses.json"
PHRASE_CATALOG = ROOT / "tests" / "data" / "phrase_catalog.json"


@dataclass
class CliRun:
    outcome: Outcome
    out: str
    err: str
    console: FakeConsole

    @property
    def code(self) -> int:
        return self.outcome.code


def cli(*argv, console: FakeConsole | None = None, provider=None) -> CliRun:
    console = console or FakeConsole()
    out, err = io.StringIO(), io.StringIO()
    kw = {"provider_factory": (lambda config: provider)} if provider is not None else {}
    outcome = run([str(a) for a in argv], console=console, out=out, err=err, **kw)
    return CliRun(outcome, out.getvalue(), err.getvalue(), console)


def write_catalog(folder: Path, cat: dict, name: str = "courses.json") -> Path:
    """A catalog built with catalog_builder (raw=True), saved where the CLI can load it."""
    path = Path(folder) / name
    path.write_text(json.dumps(cat), encoding="utf-8")
    return path
