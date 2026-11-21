"""Command-line interface.

    python -m scheduler courses.json ["prefs" | -] [--review] [--verbose] [--pick [N:]IDS] [--drop IDS]
                                     [--no-required] [--offline] [--top 5]
    python -m scheduler setup

Exit codes: 0 schedules found; 1 no solution or a conflict (still reported); 2 usage, catalog or config errors.
`run()` does the work and returns an Outcome (the code plus what was solved), so tests can check the schedules
behind the printed text; `main()` is the entry point. See plan.md, CLI.
"""
import argparse
import difflib
import getpass
import re
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, TextIO

from dotenv import dotenv_values
from pydantic import ValidationError

from scheduler.catalog import CatalogError, load_catalog
from scheduler.config import (
    DEFAULT_MODELS,
    DEFAULT_OLLAMA_URL,
    KEY_VARS,
    PROVIDERS,
    Config,
    load_config,
    user_config_path,
    write_setup,
)
from scheduler.constraints import describe
from scheduler.models import Catalog, Constraint, Schedule, SolveResult
from scheduler.parsing.base import ParseResult
from scheduler.parsing.providers import Provider, make_provider
from scheduler.parsing.router import RoutedParse, Router
from scheduler.present import PROVIDER_CHOICES, SETUP_COMMAND, either, parser_label, rule, schedule_view, unparsed_message
from scheduler.selection import Selection, SelectionError, default_modes, select_courses
from scheduler.solver import solve

TOP_MIN, TOP_MAX, TOP_DEFAULT = 1, 20, 5
BASIC_PHRASES = 'basic phrases like "no classes before 10" or "Fridays off"'
FIRST_RUN_NOTICE = (f"No LLM is set up, so this run is offline. Setup is required to understand anything beyond "
                    f"{BASIC_PHRASES}: run {SETUP_COMMAND}.")


class Console:
    """The terminal: typed answers, hidden answers, whether it's interactive, and stdin. Tests use FakeConsole."""

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def interactive(self) -> bool:
        return sys.stdin.isatty()

    def read_all(self) -> str:
        return sys.stdin.read()


@dataclass
class Outcome:
    code: int
    catalog: Catalog | None = None
    constraints: list[Constraint] = field(default_factory=list)  # everything solved with: courses + parsed rules
    routed: RoutedParse | None = None  # None when no preferences were given
    result: SolveResult | None = None  # None when nothing was solved (an error, or quitting the review)


class UsageError(Exception):
    """Bad arguments, catalog or request: printed as "Error: ..." with exit code 2, never a traceback."""


class _Exit(Exception):
    def __init__(self, code: int):
        self.code = code


# ---- arguments ----

class _ArgumentParser(argparse.ArgumentParser):
    """argparse that writes to the given streams and raises instead of calling sys.exit."""

    def __init__(self, out: TextIO, err: TextIO, **kw):
        super().__init__(**kw)
        self._out, self._err = out, err

    def error(self, message):
        raise UsageError(f"{message}\n{self.format_usage().strip()}")

    def _print_message(self, message, file=None):
        if message:
            (self._err if file is sys.stderr else self._out).write(message)

    def exit(self, status=0, message=None):
        if message:
            self._err.write(message)
        raise _Exit(status)


def _top(value: str) -> int:
    try:
        k = int(value)
    except ValueError:
        k = None
    if k is None or not TOP_MIN <= k <= TOP_MAX:
        raise argparse.ArgumentTypeError(f"must be a whole number from {TOP_MIN} to {TOP_MAX} (got {value!r})")
    return k


def _arguments(out: TextIO, err: TextIO) -> _ArgumentParser:
    p = _ArgumentParser(
        out, err, prog="python -m scheduler",
        description="Find the best clash-free weekly schedules for your courses and preferences.",
        epilog=f"First time? Run '{SETUP_COMMAND}' to set up an LLM, or continue offline (basic phrases only).",
    )
    p.add_argument("catalog", help="the courses JSON file")
    p.add_argument("prefs", nargs="?", default="",
                   help='your preferences in plain language, e.g. "no classes before 10, fridays off"; '
                        "- reads them from stdin; leave out for none")
    p.add_argument("--review", action="store_true", help="review and edit the parsed rules before solving")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list every rule in each result (met or broken), and group parsed rules by phrase")
    p.add_argument("--pick", metavar="[N:]IDS",
                   help="a pool of courses to choose from: --pick 2:CS101,CS102,CS103 takes 2; without N, takes all")
    p.add_argument("--drop", metavar="IDS", help="switch off these required courses, e.g. --drop ZGC,ECP")
    p.add_argument("--no-required", action="store_true", help="switch off every required course")
    p.add_argument("--offline", action="store_true", help="use the rule parser only for this run (saves nothing)")
    p.add_argument("--top", type=_top, default=TOP_DEFAULT, metavar="N",
                   help=f"how many schedules to show ({TOP_MIN}-{TOP_MAX}, default {TOP_DEFAULT})")
    return p


# ---- entry points ----

def main(argv: list[str] | None = None) -> int:
    return run(argv).code


def run(argv: list[str] | None = None, console: Console | None = None, out: TextIO | None = None,
        err: TextIO | None = None, provider_factory: Callable[[Config], Provider] = make_provider) -> Outcome:
    console = console or Console()
    out = out or sys.stdout
    err = err or sys.stderr
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        if argv[:1] == ["setup"]:
            if len(argv) > 1:
                raise UsageError(f"setup takes no arguments (got {' '.join(argv[1:])})")
            return Outcome(code=run_setup(console, out))
        if argv[:1] == ["web"]:
            return Outcome(code=_web(argv[1:], out, err))
        return _schedule(argv, console, out, err, provider_factory)
    except UsageError as e:
        print(f"Error: {e}", file=err)
        return Outcome(code=2)
    except _Exit as e:
        return Outcome(code=e.code)


def _web(argv, out: TextIO, err: TextIO) -> int:
    """python -m scheduler web [--port N] [--no-browser]: the page, served on this computer only."""
    from scheduler.web import server  # imported only when used: FastAPI and uvicorn aren't needed for the CLI

    p = _ArgumentParser(out, err, prog="python -m scheduler web",
                        description="Open the scheduler in your browser (served on this computer only).")
    p.add_argument("--port", type=int, help=f"the port to use (default {server.DEFAULT_PORT}, or the next free one)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = p.parse_args(argv)
    try:
        server.serve(port=args.port, open_browser=not args.no_browser, out=out)
    except server.PortInUse as e:
        raise UsageError(f"port {e.port} is in use: choose another with --port, or leave it out to use the next "
                         "free one") from None
    return 0


def _schedule(argv, console: Console, out: TextIO, err: TextIO, provider_factory) -> Outcome:
    args = _arguments(out, err).parse_args(argv)
    if args.prefs == "-" and args.review:
        raise UsageError("--review reads your commands from stdin, so the preferences can't come from stdin too: "
                         "give them as text")
    catalog = _load(args.catalog)
    selection = _courses(catalog, args)
    courses = selection.constraints
    text = (console.read_all() if args.prefs == "-" else args.prefs).strip()

    config = load_config()
    if args.offline:
        config = replace(config, provider="none", needs_setup=False)
    elif config.needs_setup:
        config = _first_run(config, console, out, may_ask=args.prefs != "-")

    router = Router(config, provider_factory=provider_factory)
    routed = router.parse(text, catalog) if text else None

    print(f"Courses: {selection.summary()}", file=out)
    print(f"Parser: {parser_label(routed, config, args.offline)}", file=out)
    print(_settings_line(config), file=out)
    if config.problem and (routed is None or config.problem not in (routed.note or "")):
        print(f"Note: {config.problem}", file=out)

    if args.review:
        review = Review(routed or RoutedParse(result=ParseResult(), path="rules"), lambda t: router.parse(t, catalog))
        if not _review(review, console, out, args.verbose):
            print("Quit without solving.", file=out)
            return Outcome(code=0, catalog=catalog, constraints=courses + review.constraints(), routed=review.routed)
        routed, parsed = review.routed, review.constraints()
    else:
        parsed = routed.result.constraints if routed else []
        if routed and routed.note:
            print(f"Note: {routed.note}", file=out)
        _print_rules(parsed, out)
        if routed and routed.result.unparsed:
            print(_unparsed_block(routed.result), file=out)

    constraints = courses + parsed
    try:
        result = solve(catalog, constraints, k=args.top)
    except ValueError as e:
        raise UsageError(f"can't solve this request: {e}") from None
    _print_result(result, catalog, args.verbose, out)
    return Outcome(code=0 if result.schedules else 1, catalog=catalog, constraints=constraints, routed=routed,
                   result=result)


def _load(path: str) -> Catalog:
    try:
        return load_catalog(path)
    except CatalogError as e:
        raise UsageError(str(e)) from None
    except OSError as e:
        raise UsageError(f"can't read the catalog {path}: {e.strerror or e}") from None


# ---- courses: required, --pick, --drop, --no-required ----

def _courses(catalog: Catalog, args) -> Selection:
    """The courses the flags choose: required ones (minus --drop / --no-required) and the --pick pool."""
    ids = {c.id.lower(): c.id for c in catalog.courses}

    def resolve(token: str, flag: str) -> str:
        t = token.strip()
        if t.lower() in ids:
            return ids[t.lower()]
        close = [ids[m] for m in difflib.get_close_matches(t.lower(), list(ids), n=3, cutoff=0.6)]
        hint = f" (did you mean {either(close)}?)" if close else ""
        raise UsageError(f"{flag}: unknown course {t!r}{hint}")

    def id_list(spec: str, flag: str) -> list[str]:
        tokens = spec.split(",")
        if not any(t.strip() for t in tokens):
            raise UsageError(f"{flag}: no course given")
        if any(not t.strip() for t in tokens):
            raise UsageError(f"{flag}: an empty course name (check the commas)")
        found = [resolve(t, flag) for t in tokens]
        twice = sorted({c for c in found if found.count(c) > 1})
        if twice:
            raise UsageError(f"{flag}: {', '.join(twice)} is listed twice")
        return found

    pool, n = [], None
    if args.pick is not None:
        spec, head = args.pick, None
        if ":" in spec:
            head, spec = spec.split(":", 1)
            try:
                n = int(head)
            except ValueError:
                raise UsageError(f"--pick: {head.strip()!r} isn't a number "
                                 "(write N:IDS, e.g. --pick 2:CS101,CS102,CS103)") from None
            if n < 1:
                raise UsageError("--pick: N must be at least 1")
        pool = id_list(spec, "--pick")
        n = n if head is not None else None

    required_ids = [c.id for c in catalog.courses if c.required]
    drop = id_list(args.drop, "--drop") if args.drop is not None else []
    for c in drop:
        if c not in required_ids:
            raise UsageError(f"--drop: {c} isn't required, so there's nothing to drop")
        if c in pool:
            raise UsageError(f"{c} is in both --drop and --pick: choose one")

    # the flags as picker modes: --no-required and --drop switch courses off, --pick puts them in the pool
    modes = default_modes(catalog)
    if args.no_required:
        modes = {c: "off" if mode == "required" else mode for c, mode in modes.items()}
    modes.update({c: "off" for c in drop})
    modes.update({c: "pool" for c in pool})
    try:
        return select_courses(catalog, modes, n if pool else None)
    except SelectionError as e:
        if str(e).startswith("no courses to schedule"):
            raise UsageError(f"{e}. Choose some with --pick, e.g. --pick 2:A,B,C") from None
        raise UsageError(f"--pick: {e}") from None


# ---- settings and the parser path ----

def _settings_line(config: Config) -> str:
    where = str(config.source) if config.source else "none saved"
    env = f"; from the environment: {', '.join(config.from_environment)}" if config.from_environment else ""
    return f"Settings: {where}{env}"


def _first_run(config: Config, console: Console, out: TextIO, may_ask: bool) -> Config:
    """Nothing set up yet: ask in a terminal, otherwise print a notice that never waits for input."""
    if not (may_ask and console.interactive()):
        print(FIRST_RUN_NOTICE, file=out)
        return config
    print(f"No LLM is set up. Without one, only {BASIC_PHRASES} are understood.", file=out)
    while True:
        try:
            answer = console.ask("Set up an LLM now? [s] set up  [o] continue offline (don't ask again)  "
                                 "[l] ask me later: ").strip().lower()
        except EOFError:
            print(file=out)  # the prompt's line was never ended
            answer = "l"
        if answer in ("s", "setup"):
            return load_config() if run_setup(console, out) == 0 else config
        if answer in ("o", "offline"):
            report = write_setup(provider="none")
            print(report.message, file=out)
            return load_config()
        if answer in ("l", "later"):
            print(f"Running offline this time; you'll be asked again next run ({SETUP_COMMAND} sets it up).", file=out)
            return config
        print("Please answer s, o or l.", file=out)


# ---- setup ----

def run_setup(console: Console, out: TextIO) -> int:
    """Choose a provider (with its model and key) or offline, and save it to the user-wide settings file."""
    target = user_config_path()
    print(f"Set up an LLM to read free-form preferences; without one, only {BASIC_PHRASES} are understood.\n"
          f"Settings are saved to {target}.", file=out)
    try:
        provider = _ask_provider(console, out)
        if provider == "none":
            report = write_setup(target, "none")
        else:
            model = _ask_model(console, out, provider)
            key = _ask_key(console, out, provider, target) if provider in KEY_VARS else None
            url = None
            if provider == "ollama":
                url = console.ask(f"Ollama URL (Enter = {DEFAULT_OLLAMA_URL}): ").strip() or None
            report = write_setup(target, provider, model=model, api_key=key, ollama_url=url)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled: nothing was saved.", file=out)
        return 1
    print(report.message, file=out)
    if report.warning:
        print(f"Warning: {report.warning}", file=out)
    return 0


CHOICES = PROVIDER_CHOICES


def _ask_provider(console: Console, out: TextIO) -> str:
    print("\n".join(f"  {i}. {label}" for i, (_, label) in enumerate(CHOICES, 1)), file=out)
    names = [name for name, _ in CHOICES]
    while True:
        answer = console.ask(f"Provider [1-{len(CHOICES)}, Enter = 1]: ").strip().lower()
        if not answer:
            return names[0]
        if answer.isdigit() and 1 <= int(answer) <= len(CHOICES):
            return names[int(answer) - 1]
        if answer in names or answer == "offline":
            return "none" if answer == "offline" else answer
        print(f"Please choose 1-{len(CHOICES)} or a name ({', '.join(PROVIDERS)}, none).", file=out)


def _ask_model(console: Console, out: TextIO, provider: str) -> str | None:
    default = DEFAULT_MODELS.get(provider)
    if default:
        return console.ask(f"Model (Enter = {default}): ").strip() or None
    while True:  # no default: the cheapest OpenAI model changes, and Ollama depends on what's installed
        model = console.ask("Model name: ").strip()
        if model:
            return model
        print(f"{provider} has no default model: give the name of the one to use.", file=out)


def _ask_key(console: Console, out: TextIO, provider: str, target) -> str | None:
    """The key, typed hidden. When setup is run again, Enter keeps the saved key (None)."""
    saved = (dotenv_values(target).get(KEY_VARS[provider]) or "").strip() if target.is_file() else ""
    prompt = "API key (Enter keeps the saved one): " if saved else "API key: "
    while True:
        key = console.secret(prompt).strip()
        if key or saved:
            return key or None
        print(f"{provider} needs an API key.", file=out)


# ---- what was understood ----

def _print_rules(parsed: list[Constraint], out: TextIO) -> None:
    if not parsed:
        print("Your rules: none", file=out)
        return
    print("Your rules:", file=out)
    for i, c in enumerate(parsed, 1):
        print(f"  {i}. {rule(c)}", file=out)


def _unparsed_block(result: ParseResult) -> str:
    return "\n".join(["Not understood:"] + [f"  - {unparsed_message(f)}" for f in result.unparsed])


# ---- review ----

REVIEW_HELP = """Commands (use the numbers in the list; several at once and ranges work: d 2 4, h 1-3):
  d N...       delete
  h N...       switch between hard and soft
  w N... W     set the weight (1-10): the last number is the weight, e.g. w 1 3 7
  t [text]     edit the text and parse it again (replaces the list and any edits)
  + phrase     add the rules in a phrase, e.g. + compact schedule
  ?            this help
  Enter        solve
  q            quit without solving"""


@dataclass
class Step:
    action: str  # "again" (show the list again), "solve", "quit" or "ask_text"
    message: str = ""


class _Refused(Exception):
    pass


class Review:
    """The numbered list of parsed rules and the commands that edit it.

    Numbers stay fixed until the text is parsed again, so `d 3 4` is exactly `d 3` then `d 4`. A command that is
    partly invalid changes nothing. Edits never re-parse; `t` and `+` call `parse` (the router).
    """

    def __init__(self, routed: RoutedParse, parse: Callable[[str], RoutedParse]):
        self.parse = parse
        self._load(routed)

    def _load(self, routed: RoutedParse) -> None:
        self.routed = routed
        self.items: dict[int, Constraint] = dict(enumerate(routed.result.constraints, 1))
        self.sources: dict[int, str] = {}  # number -> the phrase that made it, when the rules read it
        self._next = len(self.items) + 1
        self._note_sources(routed.result, list(self.items))

    def _note_sources(self, result: ParseResult, numbers: list[int]) -> None:
        for span in result.spans:
            for c in span.constraints:
                n = next((n for n in numbers if self.items[n] == c and n not in self.sources), None)
                if n is not None:
                    self.sources[n] = span.text

    def constraints(self) -> list[Constraint]:
        return [self.items[n] for n in sorted(self.items)]

    def command(self, line: str) -> Step:
        line = line.strip()
        if not line:
            return Step("solve")
        if line == "q":
            return Step("quit")
        if line in ("?", "help"):
            return Step("again", REVIEW_HELP)
        if line.startswith("+"):
            return self._add(line[1:].strip())
        op, _, rest = line.partition(" ")
        if op == "t":
            return self._retext(rest.strip()) if rest.strip() else Step("ask_text")
        if op in ("d", "h", "w"):
            try:
                return Step("again", self._edit(op, [t for t in re.split(r"[\s,]+", rest.strip()) if t]))
            except _Refused as e:
                return Step("again", f"Nothing changed: {e}.")
        return Step("again", f"Unknown command {line!r}.\n{REVIEW_HELP}")

    def _numbers(self, tokens: list[str]) -> list[int]:
        numbers = []
        for t in tokens:
            if m := re.fullmatch(r"([0-9]+)-([0-9]+)", t):
                a, b = int(m[1]), int(m[2])
                if a > b:
                    raise _Refused(f"{t} isn't a range (write the smaller number first)")
                numbers += range(a, b + 1)
            elif re.fullmatch(r"[0-9]+", t):
                numbers.append(int(t))
            else:
                raise _Refused(f"{t!r} isn't a number")
        for n in numbers:
            if numbers.count(n) > 1:
                raise _Refused(f"#{n} is listed twice")
            if n not in self.items:
                raise _Refused(f"#{n} was deleted" if 1 <= n < self._next else f"#{n} isn't in the list")
        return numbers

    def _edit(self, op: str, tokens: list[str]) -> str:
        if op == "w":
            if len(tokens) < 2:
                raise _Refused("w needs constraint numbers and a weight, e.g. w 1 3 7")
            *tokens, weight_text = tokens
            if not re.fullmatch(r"[0-9]+", weight_text):
                raise _Refused(f"{weight_text!r} isn't a number")
            weight = int(weight_text)
            if not 1 <= weight <= 10:
                raise _Refused("the weight must be a whole number from 1 to 10")
        elif not tokens:
            raise _Refused(f"{op} needs constraint numbers, e.g. {op} 2 4")
        numbers = self._numbers(tokens)

        if op == "d":
            for n in numbers:
                del self.items[n]
            return f"Deleted {', '.join(f'#{n}' for n in numbers)}."
        changed = {}
        for n in numbers:
            c = self.items[n]
            update = {"strength": "soft" if c.strength == "hard" else "hard"} if op == "h" else {"weight": weight}
            try:
                changed[n] = type(c).model_validate({**c.model_dump(), **update})
            except ValidationError:
                raise _Refused(f"#{n} ({describe(c)}) can only be {c.strength}") from None
        self.items.update(changed)
        if op == "h":
            return " ".join(f"#{n} is now {c.strength}." for n, c in changed.items())
        hard = [n for n, c in changed.items() if c.strength == "hard"]
        note = (f" A weight only counts for soft rules: {', '.join(f'#{n}' for n in hard)} "
                f"{'is' if len(hard) == 1 else 'are'} hard (h switches).") if hard else ""
        return f"Set the weight of {', '.join(f'#{n}' for n in numbers)} to {weight}.{note}"

    def _add(self, phrase: str) -> Step:
        if not phrase:
            return Step("again", "Give a phrase after +, e.g. + compact schedule")
        routed = self.parse(phrase)
        added, already = [], []
        for c in routed.result.constraints:
            if c in self.items.values():
                already.append(describe(c))
                continue
            self.items[self._next] = c
            added.append(self._next)
            self._next += 1
        self._note_sources(routed.result, added)
        messages = []
        if added:
            messages.append(f"Added {', '.join(f'#{n}' for n in added)}.")
        if already:
            messages.append(f"Already in the list: {', '.join(already)}.")
        if routed.result.unparsed:
            messages.append(_unparsed_block(routed.result))
        return Step("again", "\n".join(messages) or "Nothing added.")

    def _retext(self, text: str) -> Step:
        self._load(self.parse(text))
        return Step("again", f"Parsed the new text ({self.routed.path}): the list and any earlier edits were replaced.")

    def render(self, verbose: bool = False) -> str:
        lines = []
        if not self.items:
            lines.append("No rules (add some with + phrase).")
        current = None
        for n in sorted(self.items):
            indent = "  "
            if verbose:
                source = self.sources.get(n)
                if source != current and source is not None:
                    lines.append(f'  "{source}"')
                current = source
                indent = "     " if source is not None else "  "
            lines.append(f"{indent}{n}. {rule(self.items[n])}")
        if self.routed.result.unparsed:
            lines.append(_unparsed_block(self.routed.result))
        lines.append("Commands: d/h/w N..., t, + phrase, ? for help; Enter solves, q quits.")
        return "\n".join(lines)


def _review(review: Review, console: Console, out: TextIO, verbose: bool) -> bool:
    """Run the review loop. True to solve, False to quit."""
    while True:
        print(review.render(verbose), file=out)
        try:
            step = review.command(console.ask("review> "))
            if step.action == "ask_text":
                step = review.command("t " + console.ask("New text: "))
        except EOFError:
            print(file=out)  # the prompt's line was never ended
            return False
        if step.message:
            print(step.message, file=out)
        if step.action in ("solve", "quit"):
            return step.action == "solve"


# ---- results ----

def _print_result(result: SolveResult, catalog: Catalog, verbose: bool, out: TextIO) -> None:
    total = len(result.schedules)
    for i, s in enumerate(result.schedules, 1):
        _print_schedule(i, total, s, result.schedules[0], catalog, verbose, out)
    if result.note:
        print(f"\n{result.note}", file=out)


def _print_schedule(i: int, total: int, s: Schedule, best: Schedule, catalog: Catalog, verbose: bool,
                    out: TextIO) -> None:
    view = schedule_view(s, best, catalog)
    print(f"\nSchedule {i} of {total} (soft penalty {view['penalty']})", file=out)
    if i > 1:
        print(f"  Differs from #1: {'; '.join(view['differs']) or 'nothing visible'}", file=out)
    for x in view["sessions"]:
        line = f"  {x['day']} {x['start']}-{x['end']}  {x['label']}"
        line += f"  room {x['rooms']}" if x["rooms"] else ""
        line += f"  {x['instructors']}" if x["instructors"] else ""
        print(line, file=out)
    for note in view["notes"]:
        print(f"  Note: {note}", file=out)
    if verbose:
        print("  Rules:", file=out)
        for r in view["rules"]:
            cost = f": cost {r['cost']}" if not r["met"] else ""
            print(f"    {'met' if r['met'] else 'broken':<7}{r['text']}{cost}", file=out)
        return
    broken = [r for r in view["rules"] if not r["met"]]
    for r in broken:
        print(f"  Broken: {r['text']}: cost {r['cost']}", file=out)
    if not broken:
        print("  All rules met.", file=out)
