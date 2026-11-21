"""Plain-language text about parses and results, shared by the CLI and the web API so both say the same thing."""
from scheduler.config import Config
from scheduler.constraints import describe
from scheduler.models import Catalog, Constraint, Group, Schedule, format_time
from scheduler.parsing.base import UnparsedFragment

SETUP_COMMAND = "python -m scheduler setup"
SETUP_HINT = f'run "{SETUP_COMMAND}" to add an LLM'
# the setup choices, in the order offered (CLI and web)
PROVIDER_CHOICES = [("gemini", "Gemini (recommended; has a free tier)"), ("anthropic", "Anthropic"), ("openai", "OpenAI"),
                    ("ollama", "Ollama (runs on this computer)"), ("none", "none: continue offline")]


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


def parser_label(routed, config: Config, offline_flag: bool = False, setup_hint: str = SETUP_HINT) -> str:
    """Which parser read the text, e.g. "llm (gemini-3.5-flash-lite)" or "rules (offline): run ... to add an LLM"."""
    if routed is None:
        return "none (no preferences given)"
    path = routed.path
    if path == "llm":
        return f"llm ({config.model})"
    if path == "cache":
        return f"cache (an earlier reply from {config.model})"
    if path == "rules (offline)" and not offline_flag and not config.needs_setup:
        return f"{path}: {setup_hint}"
    return path


# Offline chosen with the page's switch while an LLM is set up: point back at the switch, not at setup.
OFFLINE_CHOSEN_NOTE = ("Offline (your choice): only simple, direct phrasing is understood. "
                       "Switch the parser back to LLM to have free-form text read as well.")

# The page has a Setup button instead of the setup command.
WEB_SETUP_HINT = "use Setup (top of the page) to add an LLM"


def for_web(text: str | None) -> str | None:
    """CLI advice reworded for the page: point at the Setup button instead of the setup command."""
    if text is None:
        return None
    return text.replace(SETUP_HINT, WEB_SETUP_HINT).replace(f"({SETUP_COMMAND})", "(Setup, top of the page)") \
               .replace(SETUP_COMMAND, "Setup (top of the page)")


def group_times(g: Group) -> str:
    """"Mon 10:00-12:00, Wed 10:00-12:00"."""
    return ", ".join(f"{s.day} {format_time(s.start)}-{format_time(s.end)}" for s in g.sessions)


def _unique(items) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


def schedule_view(s: Schedule, best: Schedule, catalog: Catalog) -> dict:
    """One schedule as display text: its sessions by day and time, notes, every rule met or broken, and how it
    differs from the best one."""
    sessions = []
    for ch in s.choices:
        label = f"{ch.course} {ch.component} {either(ch.option.group_ids)}"
        if len(ch.option.group_ids) > 1 and list(ch.register_for) != list(ch.option.group_ids):
            label += f" (register for {either(ch.register_for)})"
        for k, session in enumerate(ch.option.sessions):
            rooms = _unique(g.sessions[k].room for g in ch.option.groups)
            instructors = _unique(g.instructor for g in ch.option.groups)
            sessions.append({"day": session.day, "start": format_time(session.start), "end": format_time(session.end),
                             "course": ch.course, "component": ch.component, "label": label,
                             "rooms": either(rooms) if rooms else "", "instructors": either(instructors) if instructors else ""})
    order = {d: i for i, d in enumerate(catalog.week)}
    sessions.sort(key=lambda x: (order[x["day"]], x["start"]))
    return {
        "penalty": s.penalty,
        "sessions": sessions,
        "notes": [ch.register_note for ch in s.choices if ch.register_note],
        "rules": [{"text": rule(item.constraint), "met": item.met, "cost": 0 if item.met else item.weighted}
                  for item in s.breakdown],
        "differs": [] if s is best else differences(best, s),
    }
