"""Plain-language text about parses and results, shared by the interfaces (the CLI now, the API and UI later)."""
from scheduler.constraints import describe
from scheduler.models import Constraint, Schedule
from scheduler.parsing.base import UnparsedFragment


def either(items: list[str]) -> str:
    """["11"] -> "11"; ["11", "12"] -> "11 or 12"; ["a", "b", "c"] -> "a, b or c"."""
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def strength(c: Constraint) -> str:
    """"hard", or "soft, weight 5": a hard rule's weight never counts, so it isn't shown."""
    return "hard" if c.strength == "hard" else f"soft, weight {c.weight}"


def rule(c: Constraint) -> str:
    return f"{describe(c)} ({strength(c)})"


def unparsed_message(f: UnparsedFragment) -> str:
    """Why a fragment wasn't used, and what to do about it."""
    text = f'"{f.text}"'
    match f.reason:
        case "unknown_name":
            hint = f"; did you mean {either(f.candidates)}?" if f.candidates else "."
            return f"{text}: not a name in the catalog{hint}"
        case "ambiguous_name":
            options = f" could mean {either(f.candidates)}" if f.candidates else " matches several names"
            return f"{text}:{options}; say which."
        case "invalid_time":
            return f"{text}: an impossible time or range."
        case "conflicting_strength":
            return f'{text}: mixes must-have and nice-to-have wording (like "must" and "ideally"); keep one.'
        case "unclear_or":
            return f'{text}: "or" is unclear here (one of them, or all?). Use "and" for all of them, or name one.'
    return f'{text}: not understood. Try rephrasing, e.g. "no classes before 10".'


def differences(best: Schedule, other: Schedule) -> list[str]:
    """How `other` differs from the best schedule, e.g. "CS101 lab 21 or 22 instead of 11 or 12"."""
    def options(s: Schedule) -> dict:
        return {(ch.course, ch.component): ch.option.group_ids for ch in s.choices}

    a, b = options(best), options(other)
    a_courses = {course for course, _ in a}
    b_courses = {course for course, _ in b}
    found = []
    for (course, component), groups in b.items():
        old = a.get((course, component))
        if old is not None and old != groups:
            found.append(f"{course} {component} {either(groups)} instead of {either(old)}")
    found += [f"adds {c}" for c in sorted(b_courses - a_courses)]
    found += [f"drops {c}" for c in sorted(a_courses - b_courses)]
    return found
