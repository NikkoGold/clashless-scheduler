"""LLMParser: the whole text to a model, back as typed constraints. Built to keep each call cheap (see plan.md, Parsing).

- A flat output schema: one shape for every constraint, always with its strength (a missing one would read as hard). The provider only checks that shape; each item is then checked
  by the real Pydantic models, which stay the only definition of what each kind needs.
- Fixed instructions first (the same for every call, so providers can cache them), then the catalog's names (no
  session times or rooms), then the user's text. Defaults are left out of the reply, and there are no source spans.
- One retry when the reply is invalid, with the error. Provider errors aren't retried, except an overloaded provider
  (HTTP 503): wait and try again a few times, each try spent from the budget.
- Names are checked against the catalog: a wrong one goes to `unparsed`, and a wrong case is corrected.
"""
import difflib
import json
import time
from typing import Protocol, get_args

from pydantic import ValidationError

from scheduler.constraints import describe
from scheduler.models import Catalog, Constraint, Day, parse_constraint
from scheduler.parsing.base import ParseResult, UnparsedFragment, UnparsedReason
from scheduler.parsing.providers import Provider

KINDS = [m.model_fields["kind"].default for m in get_args(get_args(Constraint)[0])]
DAYS = list(get_args(Day))
_TEXT = {"type": "string"}
_DAY = {"type": "string", "enum": DAYS}
# parameters the model sees under a clearer name: "name" was read as the constraint's own name ("name": "prefer_group")
LLM_FIELD_NAMES = {"name": "instructor"}
CONSTRAINT_PARAMS = {
    "kind": {"type": "string", "enum": KINDS},
    "strength": {"type": "string", "enum": ["hard", "soft"]},
    "weight": {"type": "integer"},
    "course": _TEXT,
    "courses": {"type": "array", "items": _TEXT},
    "n": {"type": "number"},
    "h": {"type": "integer"},
    "minutes": {"type": "integer"},
    "time": _TEXT,
    "start": _TEXT,
    "end": _TEXT,
    "day": _DAY,
    "days": {"type": "array", "items": _DAY},
    "component": _TEXT,
    "group": _TEXT,
    "instructor": _TEXT,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "constraints": {"type": "array", "items": {"type": "object", "properties": CONSTRAINT_PARAMS, "required": ["kind", "strength"]}},
        "unparsed": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": _TEXT, "reason": {"type": "string", "enum": list(get_args(UnparsedReason))}},
            "required": ["text", "reason"],
        }},
    },
    "required": ["constraints", "unparsed"],
}

SYSTEM_PROMPT = """\
You turn a student's timetable preferences into constraints for a scheduling solver. Reply with JSON matching the schema.

Constraint kinds (parameters in brackets, ? = optional):
- must_take(course), exclude_course(course), take_n_of(courses, n): which courses to take
- no_class_before(time, days?), no_class_after(time, days?): time as "HH:MM"; leave days out for every day
- day_off(day); max_days(n): at most n days with classes; max_hours_per_day(h)
- free_block(start, end, day?): keep that time free; leave day out for every day
- max_gap(minutes): longest idle time between classes on a day ("no gaps" is 0)
- compact: keep idle time small (soft only; a hard version is max_gap 0)
- prefer_group(course, component, group), avoid_group(course, component, group)
- prefer_instructor(instructor), avoid_instructor(instructor)
- min_credits(n), max_credits(n)

Give each constraint only the parameters its kind takes, plus its strength: "hard" for must, can't, never, need, \
no; "soft" for prefer, ideally, if possible, avoid, rather. No strength word means hard. Leave weight out (default 5) \
unless the user says how much something matters (1-10).

Conventions:
- Days are Sun Mon Tue Wed Thu Fri Sat. "Every day" and "weekdays" use the week given below; the weekend is Fri and \
Sat whatever the week, and weekdays are the week's other days.
- A bare hour 1-7 means pm ("after 4" is 16:00); 8-12 is taken as given. Noon is 12:00. Midnight is not a time.
- Unless times are given, lunch is 12:00-13:00.
- On a course, "prefer" is a soft must_take and "avoid" a soft exclude_course.
- "Take <course> <group>" is a must_take plus a hard prefer_group: take the course, and only in that group.
- Use only the course ids, components, groups and instructors listed below, spelled as listed.

Put anything you can't turn into a constraint in "unparsed": the user's exact words, and a reason (not_understood, \
unknown_name for a course or person that isn't listed, ambiguous_name, invalid_time, conflicting_strength). Don't guess.

Example: "ideally nothing before 10, Fridays off, my bus is slow"
{"constraints": [{"kind": "no_class_before", "time": "10:00", "strength": "soft"}, {"kind": "day_off", "day": "Fri", \
"strength": "hard"}], \
"unparsed": [{"text": "my bus is slow", "reason": "not_understood"}]}"""

RETRY = "\n\nYour previous reply was invalid: {error}\nReply again with corrected JSON."
MAX_ERROR = 300  # characters of a validation error kept in a message
BUSY_WAITS = (2, 5, 10)  # seconds to wait before each try again when the provider is overloaded (HTTP 503)


class Budget(Protocol):
    def try_spend(self) -> bool: ...


class LLMFailed(Exception):
    """The LLM couldn't give a usable parse. The message says why, for the user."""

    def __init__(self, message: str, provider_error: bool = False):
        super().__init__(message)
        self.provider_error = provider_error  # the provider failed (network, API, model name), not the model's reply


def user_message(text: str, catalog: Catalog) -> str:
    lines = [f"Week: {', '.join(catalog.week)}", "Courses (id: name; components and groups):"]
    instructors: dict[str, None] = {}
    for c in catalog.courses:
        parts = [f"{name} {', '.join(g.group for g in groups)}" for name, groups in c.components.items()]
        lines.append(f"- {c.id}: {c.name}; {'; '.join(parts)}")
        instructors.update((g.instructor, None) for gs in c.components.values() for g in gs if g.instructor)
    lines.append(f"Instructors: {', '.join(instructors) or 'none listed'}")
    lines += ["", "User's text:", text]
    return "\n".join(lines)


class LLMParser:
    def __init__(self, provider: Provider, budget: Budget | None = None, sleep=time.sleep):
        self.provider = provider
        self.budget = budget  # None: no limit
        self.sleep = sleep

    def parse(self, text: str, catalog: Catalog) -> ParseResult:
        first = user_message(text, catalog)
        message, error = first, None
        for _ in range(2):
            if self.budget is not None and not self.budget.try_spend():
                if error is None:
                    raise LLMFailed("the daily limit of LLM calls is reached")
                raise LLMFailed(f"the reply was invalid ({error}), and the daily limit leaves no call to retry")
            raw = self._complete(message)
            try:
                constraints, unparsed = _read_reply(raw)
            except _Invalid as e:
                error = str(e)[:MAX_ERROR]
                message = first + RETRY.format(error=error)
                continue
            return _check_names(constraints, unparsed, catalog)
        raise LLMFailed(f"the reply was invalid twice ({error})")

    def _complete(self, message: str) -> str:
        """One call to the provider. Network, API and SDK errors fail at once, except an overload (503), which
        usually clears within seconds: wait and try again, while the waits and the budget last."""
        waits = iter(BUSY_WAITS)
        while True:
            try:
                return self.provider.complete(SYSTEM_PROMPT, message, OUTPUT_SCHEMA)
            except Exception as e:
                wait = next(waits, None) if _http_status(e) == 503 else None
                if wait is None or (self.budget is not None and not self.budget.try_spend()):
                    raise LLMFailed(f"{self.provider.name} error: {str(e)[:MAX_ERROR]}", provider_error=True) from e
                self.sleep(wait)


def _http_status(e: Exception) -> int | None:
    """The HTTP status of an SDK error: `code` (google-genai), `status_code` (anthropic, openai) or
    `response.status_code` (httpx, used for Ollama)."""
    for value in (getattr(e, "code", None), getattr(e, "status_code", None),
                  getattr(getattr(e, "response", None), "status_code", None)):
        if isinstance(value, int):
            return value
    return None


# ---- reading the reply ----

class _Invalid(Exception):
    pass


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())


def _read_reply(raw: str) -> tuple[list[Constraint], list[UnparsedFragment]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise _Invalid(f"not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise _Invalid("the reply must be a JSON object with 'constraints' and 'unparsed'")
    items, frags = data.get("constraints"), data.get("unparsed", [])
    if not isinstance(items, list) or not isinstance(frags, list):
        raise _Invalid("'constraints' and 'unparsed' must both be lists")

    problems, constraints, unparsed = [], [], []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            problems.append(f"constraint {i} isn't an object")
            continue
        item = {k: v for k, v in item.items() if v is not None}
        if "strength" not in item:  # left out, it would silently read as hard
            problems.append(f"constraint {i} ({item.get('kind')}): strength is required")
            continue
        if item.get("kind") in ("prefer_instructor", "avoid_instructor") and "instructor" in item:
            item["name"] = item.pop("instructor")
        try:
            constraints.append(parse_constraint(item))
        except ValidationError as e:
            problems.append(f"constraint {i} ({item.get('kind')}): {_errors(e)}")
    for i, frag in enumerate(frags, 1):
        try:
            unparsed.append(UnparsedFragment.model_validate(frag))
        except ValidationError as e:
            problems.append(f"unparsed {i}: {_errors(e)}")
    if problems:
        raise _Invalid("; ".join(problems))
    return constraints, unparsed


# ---- names must be in the catalog ----

class _Unknown(Exception):
    def __init__(self, what: str, name: str, candidates: list[str], reason: UnparsedReason = "unknown_name"):
        super().__init__(f"{what} {name}")
        self.what, self.name, self.candidates, self.reason = what, name, candidates, reason


def _fix(name: str, options: list[str], what: str, suggest_all: bool = False) -> str:
    """The catalog's spelling of `name`: exact first, then case-insensitive if only one fits; else _Unknown."""
    if name in options:
        return name
    same = [o for o in options if o.lower() == name.lower()]
    if len(same) == 1:
        return same[0]
    if same:  # "Ab" when the catalog has both "ab" and "AB"
        raise _Unknown(what, name, same, reason="ambiguous_name")
    close = options if suggest_all else difflib.get_close_matches(name, options, n=3, cutoff=0.6)
    raise _Unknown(what, name, list(close))


def _resolve(c: Constraint, catalog: Catalog) -> Constraint:
    courses = {course.id: course for course in catalog.courses}
    ids = list(courses)
    match c.kind:
        case "must_take" | "exclude_course":
            return c.model_copy(update={"course": _fix(c.course, ids, "course")})
        case "take_n_of":
            return c.model_copy(update={"courses": [_fix(x, ids, "course") for x in c.courses]})
        case "prefer_group" | "avoid_group":
            course = courses[_fix(c.course, ids, "course")]
            component = _fix(c.component, list(course.components), f"{course.id} component", suggest_all=True)
            groups = [g.group for g in course.components[component]]
            group = _fix(c.group, groups, f"{course.id} {component} group", suggest_all=True)
            return c.model_copy(update={"course": course.id, "component": component, "group": group})
        case "prefer_instructor" | "avoid_instructor":
            names = list(dict.fromkeys(g.instructor for co in catalog.courses for gs in co.components.values()
                                       for g in gs if g.instructor))
            return c.model_copy(update={"name": _fix(c.name, names, "instructor")})
    return c


def _check_names(constraints: list[Constraint], unparsed: list[UnparsedFragment], catalog: Catalog) -> ParseResult:
    result = ParseResult(unparsed=list(unparsed))
    seen: set[str] = set()
    for c in constraints:
        try:
            c = _resolve(c, catalog)
        except _Unknown as e:
            why = f"no {e.what} {e.name} in the catalog" if e.reason == "unknown_name" else f"which {e.what} is {e.name}?"
            result.unparsed.append(UnparsedFragment(text=f"{describe(c)} ({why})", reason=e.reason, candidates=e.candidates))
            continue
        key = c.model_dump_json()
        if key not in seen:  # the same rule twice would double a soft penalty
            seen.add(key)
            result.constraints.append(c)
    return result
