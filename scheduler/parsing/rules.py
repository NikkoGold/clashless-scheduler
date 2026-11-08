"""Rule-based parser: always runs first, and is the only parser in offline mode.

Deliberately simple and it never guesses: the text is split into clauses, and each clause either
matches one pattern completely or is returned whole in `unparsed`, with a reason. See plan.md, Parsing.

Per clause: normalise the wording, note hard/soft words, pull out the days ("on Mon and Wed",
"except Tue", "weekends"), the time ranges ("12-13", "between 12 and 1") and single times
("before 10"), drop filler words, then match what's left against a fixed set of patterns.
"""
import difflib
import re
from dataclasses import dataclass

from pydantic import ValidationError

from scheduler.models import (
    AvoidGroup,
    AvoidInstructor,
    Catalog,
    Compact,
    Constraint,
    DayOff,
    ExcludeCourse,
    FreeBlock,
    MaxCredits,
    MaxDays,
    MaxGap,
    MaxHoursPerDay,
    MinCredits,
    MustTake,
    NoClassAfter,
    NoClassBefore,
    PreferGroup,
    PreferInstructor,
    format_time,
)
from scheduler.parsing.base import ParseResult, Span, UnparsedFragment

CALENDAR_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEKEND = ["Fri", "Sat"]  # whatever the catalog's week; "weekdays" are the week's other days
LUNCH = ("12:00", "13:00")

DAY_WORDS = {
    "Sun": ["sun", "suns", "sunday", "sundays"],
    "Mon": ["mon", "mons", "monday", "mondays"],
    "Tue": ["tue", "tues", "tuesday", "tuesdays"],
    "Wed": ["wed", "weds", "wednesday", "wednesdays"],
    "Thu": ["thu", "thur", "thurs", "thursday", "thursdays"],
    "Fri": ["fri", "fris", "friday", "fridays"],
    "Sat": ["sat", "sats", "saturday", "saturdays"],
}
DAY_OF = {word: day for day, words in DAY_WORDS.items() for word in words}
DAY_GROUPS = {"weekday": "weekdays", "weekdays": "weekdays", "weekend": "weekend", "weekends": "weekend",
              "daily": "all", "everyday": "all"}
NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
TITLES = {"prof", "professor", "dr", "mr", "mrs", "ms"}
GROUP_WORDS = {"group", "section", "sec"}
# used only when the catalog has the full component name
COMPONENT_ALIASES = {"lec": "lecture", "tut": "tutorial", "recitation": "tutorial"}
ABBREVIATIONS = TITLES | {"sec", "lec", "tut"}  # a dot after these doesn't end a clause

HARD_WORDS = r"\b(?:must|cant|never|need|needs|have to|has to|got to)\b"
SOFT_WORDS = r"\b(?:prefer|preferably|ideally|if possible|avoid|rather|would like|hopefully|try to)\b"
# hard/soft wording that carries no other meaning, removed once the strength is noted
MODIFIERS = ["if possible", "would like to", "would like", "would rather", "have to", "has to", "got to",
             "need to", "needs to", "try to", "preferably", "ideally", "hopefully", "must", "needs", "need",
             "rather"]
NEGATIONS = {"no", "not", "nothing", "never", "cant", "dont", "avoid", "without", "none", "wont"}
FILLER = {"please", "thanks", "thank", "you", "i", "am", "would", "like", "to", "a", "an", "the", "my", "me",
          "any", "have", "having", "be", "is", "are", "also", "really", "keep", "classes", "class",
          "lectures", "lessons", "schedule", "course", "courses", "for", "of", "in", "want", "so", "just",
          "per", "it", "that", "we", "ok", "okay", "hi", "hello"}
LIST_WORDS = {"and", "or"}
EXPLICIT_NAME_VERBS = {"prefer", "avoid", "never", "take", "include", "add", "drop", "skip", "exclude", "remove"}

_TIME = r"(?:noon|midday|midnight|\d{1,2}(?:[:.]\d{2})?(?:\s?(?:am|pm))?)"
_T = rf"(?<![\w:.])({_TIME})(?![\w:])"
RANGE_PATTERNS = [
    re.compile(rf"\bbetween\s+{_T}\s+and\s+{_T}"),
    re.compile(rf"\bfrom\s+{_T}\s+(?:to|until|till|-)\s+{_T}"),
    re.compile(rf"{_T}\s*(?:-|to|until|till)\s*{_T}"),
]
SINGLE_TIME = re.compile(rf"\b(before|after|by|until|till|past|at)\s+{_T}")
SPLIT_AT = re.compile(r"[;\n,]|\.(?=\s|$)|\.(?=[^\d])|\band\b", re.IGNORECASE)


class _Unparsed(Exception):
    def __init__(self, reason: str, candidates: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.candidates = candidates or []


@dataclass(frozen=True)
class _Name:
    kind: str  # "course" or "instructor"
    value: str  # course id or instructor name
    key: str  # what was matched, normalised


class RuleParser:
    def parse(self, text: str, catalog: Catalog) -> ParseResult:
        names = _names(catalog)
        result = ParseResult()
        seen: set[str] = set()
        for clause in _split(text, names):
            try:
                constraints = _parse_clause(clause, catalog, names)
            except _Unparsed as e:
                result.unparsed.append(UnparsedFragment(text=clause, reason=e.reason, candidates=e.candidates))
                continue
            if constraints is None:
                continue  # filler only, such as "thanks"
            result.spans.append(Span(text=clause, constraints=constraints))
            for c in constraints:
                key = c.model_dump_json()
                if key not in seen:  # the same rule twice would double a soft penalty
                    seen.add(key)
                    result.constraints.append(c)
        return result


# ---- names in the catalog ----

def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def _names(catalog: Catalog) -> list[_Name]:
    found = []
    for c in catalog.courses:
        found.append(_Name("course", c.id, _normalise(c.id)))
        found.append(_Name("course", c.id, _normalise(c.name)))
    instructors = {g.instructor for c in catalog.courses for gs in c.components.values() for g in gs}
    found += [_Name("instructor", i, _normalise(i)) for i in sorted(instructors - {None})]
    return found


def _label(name: _Name) -> str:
    return name.value


def _resolve(item: str, names: list[_Name]) -> _Name:
    """One catalog name for `item`, or _Unparsed (ambiguous_name / unknown_name with suggestions)."""
    words = [w for w in item.split() if w not in TITLES]
    query = " ".join(words)
    if not query:
        raise _Unparsed("not_understood")

    def one(matches: list[_Name]) -> _Name | None:
        distinct = {(m.kind, m.value): m for m in matches}
        if len(distinct) == 1:
            return next(iter(distinct.values()))
        if len(distinct) > 1:
            raise _Unparsed("ambiguous_name", sorted({_label(m) for m in distinct.values()}))
        return None

    exact = one([n for n in names if n.key == query])
    if exact:
        return exact
    # every word of the query appears in one course name: "algorithms", "intro", "ground vehicle"
    contained = one([n for n in names if n.kind == "course" and set(words) <= set(n.key.split())])
    if contained:
        return contained

    ratios: dict[tuple, float] = {}
    for n in names:
        r = difflib.SequenceMatcher(None, query, n.key).ratio()
        ratios[(n.kind, n.value)] = max(r, ratios.get((n.kind, n.value), 0))
    ranked = sorted(ratios.items(), key=lambda kv: -kv[1])
    best = ranked[0] if ranked else None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best and best[1] >= 0.85:
        if best[1] - runner_up >= 0.1:
            return next(n for n in names if (n.kind, n.value) == best[0])
        close = [kv[0][1] for kv in ranked if best[1] - kv[1] < 0.1]
        raise _Unparsed("ambiguous_name", sorted(close))
    raise _Unparsed("unknown_name", [kv[0][1] for kv in ranked if kv[1] >= 0.6])


# ---- splitting into clauses ----

def _split(text: str, names: list[_Name]) -> list[str]:
    """Clauses as they appear in the text, so unparsed fragments and spans quote the user exactly."""
    name_starts = {n.key.split()[0] for n in names if n.key} | TITLES
    name_ends = {n.key.split()[-1] for n in names if n.key}
    cuts, start = [], 0
    for m in SPLIT_AT.finditer(text):
        if _keeps_together(text, m, name_starts, name_ends):
            continue
        cuts.append((start, m.start()))
        start = m.end()
    cuts.append((start, len(text)))
    clauses = []
    for a, b in cuts:
        piece = text[a:b].strip(" \t\r\n!?\"'()")
        piece = re.sub(r"^(?:and|also)\s+", "", piece, flags=re.IGNORECASE)  # ", and fridays off"
        if piece:
            clauses.append(piece)
    return clauses


def _word_before(text: str, pos: int) -> str:
    m = re.search(r"([\w.]+)[^\w]*$", text[:pos])
    return m.group(1).lower() if m else ""


def _word_after(text: str, pos: int) -> str:
    m = re.match(r"[^\w]*(\w+)", text[pos:])
    return m.group(1).lower() if m else ""


def _keeps_together(text: str, m: re.Match, name_starts: set[str], name_ends: set[str]) -> bool:
    sep = m.group(0)
    before, after = _word_before(text, m.start()), _word_after(text, m.end())
    if sep == ".":
        word = re.search(r"(\w+)$", text[: m.start()])
        prev = word.group(1).lower() if word else ""
        # "Prof. Levi", "a.m."; a dot between digits ("4.30") never matches SPLIT_AT
        return prev in ABBREVIATIONS or prev in ("a", "p", "m") or text[m.start() - 2: m.start()].lower() in ("a.", "p.")
    before = before.rstrip(".")
    if _is_day_word(before) and _is_day_word(after):
        return True  # "Mon, Wed and Fri off"
    if sep.lower() == "and" and re.search(rf"\bbetween\s+{_TIME}\s*$", text[: m.start()], re.IGNORECASE):
        return True  # "between 12 and 1"
    return before in name_ends and after in name_starts  # "prefer Cohen and Adler"


def _is_day_word(word: str) -> bool:
    return word in DAY_OF or word in DAY_GROUPS


# ---- one clause ----

def _parse_clause(clause: str, catalog: Catalog, names: list[_Name]) -> list[Constraint] | None:
    s = _normalise_clause(clause)
    strength = _strength(s)
    explicit_verb = bool(set(s.split()) & EXPLICIT_NAME_VERBS)
    for phrase in MODIFIERS:
        s = re.sub(rf"\b{phrase}\b", " ", s)

    s, days = _take_days(s, catalog)
    s, ranges = _take_ranges(s)
    s, times = _take_times(s)

    words = ["NEG" if w in NEGATIONS else w for w in s.split()]
    words = [w for i, w in enumerate(words) if not (w == "NEG" and i and words[i - 1] == "NEG")]
    words = [w for w in words if w not in FILLER]
    if not words and days is None:
        return None  # nothing but filler

    has_placeholders = days is not None or ranges or times
    try:
        found = _match_rules(words, days, ranges, times, strength)
        if found is not None:
            return found
        if has_placeholders:
            raise _Unparsed("not_understood")
        return _match_names(words, catalog, names, strength, explicit_verb)
    except ValidationError:
        raise _Unparsed("not_understood") from None  # e.g. "at most 9 days" in a 6-day week


def _normalise_clause(clause: str) -> str:
    s = clause.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\b(?:can't|cannot|can not|cant)\b", " cant ", s)
    s = re.sub(r"\b(?:don't|do not|dont)\b", " dont ", s)
    s = re.sub(r"\bwon't\b", " wont ", s)
    s = re.sub(r"\bi'd\b", " i would ", s)
    s = re.sub(r"\bi'm\b", " i am ", s)
    s = re.sub(r"\b([ap])\.m\.?", r"\1m", s)
    s = re.sub(r"(?<=\d)\s+([ap]m)\b", r"\1", s)
    for word, n in NUMBER_WORDS.items():
        s = re.sub(rf"\b{word}\b", str(n), s)
    s = re.sub(r"\b(?:at most|at the most|no more than|not more than|maximum|max|up to|only)\b", " MAX ", s)
    s = re.sub(r"\b(?:at least|at the least|no less than|not less than|no fewer than|minimum)\b", " MIN ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)  # keep "4.30", drop sentence dots
    s = re.sub(r"[^\w:.\- ]", " ", s)
    s = re.sub(r"(?<![\d\s])-|-(?![\d\s])", " ", s)  # keep "12-13", drop other hyphens
    return " ".join(s.split())


def _strength(s: str) -> str | None:
    hard, soft = bool(re.search(HARD_WORDS, s)), bool(re.search(SOFT_WORDS, s))
    if hard and soft:
        raise _Unparsed("conflicting_strength")
    return "hard" if hard else "soft" if soft else None


# ---- days ----

@dataclass
class _Days:
    explicit: list[str] | None  # specific days, or None for "every day"
    is_all: bool = False


def _take_days(s: str, catalog: Catalog) -> tuple[str, _Days | None]:
    """Pull out one run of day words ("on Mon and Wed", "except Tue", "every day"), if there is one."""
    s = re.sub(r"\b(?:every|each) day\b", " everyday ", s)
    words = s.split()
    run: list[int] = []
    for i, w in enumerate(words):
        if _is_day_word(w):
            run.append(i)
    if not run:
        return s, None
    first, last = run[0], run[-1]
    if any(not (_is_day_word(w) or w in LIST_WORDS or w == "every") for w in words[first:last + 1]):
        raise _Unparsed("not_understood")  # two separate day mentions
    lead = first
    if lead > 0 and words[lead - 1] in ("on", "every"):
        lead -= 1
    except_ = lead > 0 and words[lead - 1] in ("except", "but")  # "except Tue", "except on Tue"
    if except_:
        lead -= 1
        if lead > 0 and words[lead - 1] == "every":
            lead -= 1

    picked: set[str] = set()
    is_all = False
    for w in words[first:last + 1]:
        if w in DAY_OF:
            picked.add(DAY_OF[w])
        elif DAY_GROUPS.get(w) == "weekend":
            picked.update(WEEKEND)
        elif DAY_GROUPS.get(w) == "weekdays":
            picked.update(d for d in catalog.week if d not in WEEKEND)
        elif DAY_GROUPS.get(w) == "all":
            is_all = True
    if is_all and (picked or except_):
        raise _Unparsed("not_understood")
    if except_:
        days = _Days([d for d in catalog.week if d not in picked])
        if not days.explicit:
            raise _Unparsed("not_understood")
    elif is_all:
        days = _Days(None, is_all=True)
    else:
        days = _Days(sorted(picked, key=CALENDAR_ORDER.index))
    if days.explicit == []:
        raise _Unparsed("not_understood")  # e.g. "weekdays" in a week that is only Fri and Sat
    rest = words[:lead] + ["DAYS"] + words[last + 1:]
    return " ".join(rest), days


# ---- times ----

@dataclass
class _Lit:
    minutes: int
    suffix: str | None  # "am", "pm", or None
    exact: bool  # noon, midnight, or a 24-hour time (13:00 and up, or 0)


def _lit(text: str) -> _Lit:
    text = text.replace(" ", "")
    if text in ("noon", "midday"):
        return _Lit(12 * 60, None, True)
    if text == "midnight":
        return _Lit(0, None, True)
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(am|pm)?", text)
    if not m:
        raise _Unparsed("invalid_time")
    h, mins, suffix = int(m[1]), int(m[2] or 0), m[3]
    if mins > 59:
        raise _Unparsed("invalid_time")
    if suffix:
        if not 1 <= h <= 12:
            raise _Unparsed("invalid_time")
        return _Lit((h % 12 + (12 if suffix == "pm" else 0)) * 60 + mins, suffix, True)
    if h > 23:
        raise _Unparsed("invalid_time")
    return _Lit(h * 60 + mins, None, h >= 13 or h == 0)


def _bare(lit: _Lit) -> int:
    """A bare 1-7 means pm; 8-12 is taken as given."""
    if lit.exact or lit.suffix:
        return lit.minutes
    return lit.minutes + 12 * 60 if 1 <= lit.minutes // 60 <= 7 else lit.minutes


def _range(start_text: str, end_text: str) -> tuple[int, int]:
    start, end = _lit(start_text), _lit(end_text)
    e = _bare(end)
    if not start.exact and not start.suffix and end.suffix:
        # "8-10pm" -> 20-22, but "11-1pm" -> 11-13: borrow the end's am/pm only if start stays first
        borrowed = (start.minutes // 60 % 12 + (12 if end.suffix == "pm" else 0)) * 60 + start.minutes % 60
        s = borrowed if borrowed < e else _bare(start)
    else:
        s = _bare(start)
        if s != start.minutes and s >= e:
            s = start.minutes  # "7-9" reads as 07-09 rather than an impossible 19-09
    if s >= e:
        raise _Unparsed("invalid_time")
    return s, e


def _take_ranges(s: str) -> tuple[str, list[tuple[int, int]]]:
    found = []
    for pattern in RANGE_PATTERNS:
        def repl(m):
            found.append(_range(m.group(1), m.group(2)))
            return " RANGE "
        s = pattern.sub(repl, s)
    return " ".join(s.split()), found


def _take_times(s: str) -> tuple[str, list[int]]:
    found = []

    def repl(m):
        found.append(_bare(_lit(m.group(2))))
        return f" {m.group(1)} TIME "
    s = SINGLE_TIME.sub(repl, s)
    return " ".join(s.split()), found


# ---- matching ----

def _apply(strength: str | None, default: str = "hard") -> str:
    return strength or default


def _match_rules(words, days: _Days | None, ranges, times, strength) -> list[Constraint] | None:
    """Rules about times, days, load, gaps and credits; None if nothing here matches."""
    core = " ".join(w for w in words if w not in ("prefer", "DAYS") and w not in LIST_WORDS)
    st = _apply(strength)
    day_list = days.explicit if days else None
    m = None

    def one_time():
        return format_time(times[0])

    if len(times) == 1 and not ranges:
        if re.fullmatch(r"NEG before TIME|start (?:after|at) TIME", core):
            return [NoClassBefore(time=one_time(), days=day_list, strength=st)]
        if re.fullmatch(r"NEG after TIME|(?:finish|finished|done|end) (?:by|before|at) TIME", core):
            return [NoClassAfter(time=one_time(), days=day_list, strength=st)]
        return None
    if times:
        return None

    if ranges:
        if len(ranges) != 1:
            return None
        start, end = (format_time(t) for t in ranges[0])
        if re.fullmatch(r"RANGE free|free RANGE|NEG RANGE|lunch(?: break)? RANGE|RANGE lunch(?: break)?", core):
            targets = day_list if day_list else [None]
            return [FreeBlock(day=d, start=start, end=end, strength=st) for d in targets]
        return None

    if days is not None:
        if day_list and re.fullmatch(r"off|NEG|free|off free|NEG off", core):
            return [DayOff(day=d, strength=st) for d in day_list]
        return None

    if re.fullmatch(r"lunch(?: break)?(?: free)?|free lunch", core):
        return [FreeBlock(start=LUNCH[0], end=LUNCH[1], strength=st)]
    if m := re.fullmatch(r"MAX (\d+) days?(?: week)?", core):
        return [MaxDays(n=int(m[1]), strength=st)]
    if m := re.fullmatch(r"MAX (\d+) hours? day", core):
        return [MaxHoursPerDay(h=int(m[1]), strength=st)]
    if re.fullmatch(r"NEG gaps?", core):
        return [MaxGap(minutes=0, strength=st)]
    unit = r"(\d+) (hours?|hrs?|minutes?|mins?)"
    if m := re.fullmatch(rf"MAX gaps? {unit}|gaps? MAX {unit}|NEG gaps? (?:longer|more) than {unit}", core):
        n, u = [g for g in m.groups() if g is not None]
        return [MaxGap(minutes=int(n) * (60 if u.startswith("h") else 1), strength=st)]
    if re.fullmatch(r"compact|(?:minimi[sz]e|few|fewer) gaps?", core):
        return [MaxGap(minutes=0, strength="hard")] if strength == "hard" else [Compact()]
    if m := re.fullmatch(r"MIN (\d+(?:\.\d+)?) credits?", core):
        return [MinCredits(n=float(m[1]), strength=st)]
    if m := re.fullmatch(r"MAX (\d+(?:\.\d+)?) credits?", core):
        return [MaxCredits(n=float(m[1]), strength=st)]
    return None


def _match_names(words, catalog: Catalog, names, strength, explicit_verb) -> list[Constraint]:
    """Course, instructor and group rules: "prefer Cohen", "drop CS101", "prefer CS101 lab 11"."""
    if not words:
        raise _Unparsed("not_understood")
    positive = words[0] == "prefer"
    if positive:
        words = words[1:]
        if words and words[0] == "NEG":
            positive, words = False, words[1:]
    head = words[0] if words else ""
    if head == "NEG" and len(words) > 1 and words[1] == "take":
        mode, rest = "drop", words[2:]
    elif head in ("take", "include", "add"):
        mode, rest = "take", words[1:]
    elif head in ("drop", "skip", "exclude", "remove"):
        mode, rest = "drop", words[1:]
    elif head == "NEG":
        mode, rest = "avoid", words[1:]
    elif positive:
        mode, rest = "prefer", words
    else:
        raise _Unparsed("not_understood")
    if not rest:
        raise _Unparsed("not_understood")

    group_rules = _match_group(rest, catalog, names, mode, strength)
    if group_rules is not None:
        return group_rules

    items, current = [], []
    for w in rest + ["and"]:
        if w in LIST_WORDS:
            if current:
                items.append(" ".join(current))
            current = []
        else:
            current.append(w)
    if not items:
        raise _Unparsed("not_understood")

    found = []
    for item in items:
        try:
            name = _resolve(item, names)
        except _Unparsed as e:
            if e.reason == "unknown_name" and not explicit_verb:
                raise _Unparsed("not_understood") from None
            raise
        found.append(_rule_for(name, mode, strength))
    return found


def _rule_for(name: _Name, mode: str, strength) -> Constraint:
    if name.kind == "course":
        if mode in ("take", "prefer"):
            return MustTake(course=name.value, strength=_apply(strength))
        return ExcludeCourse(course=name.value, strength=_apply(strength))
    if mode == "prefer":
        return PreferInstructor(name=name.value, strength=_apply(strength, "soft"))
    if mode == "avoid":
        return AvoidInstructor(name=name.value, strength=_apply(strength))
    raise _Unparsed("not_understood")  # "take Cohen", "drop Cohen"


def _is_course(text: str, names) -> bool:
    try:
        return _resolve(text, names).kind == "course"
    except _Unparsed:
        return False


def _group_reference(rest, is_component) -> tuple[int, int, str | None, str] | None:
    """Find `[component] [group|section|sec] ID` in the words: (start, end, component word, ID)."""
    for i, w in enumerate(rest):
        if not (is_component(w) or w in GROUP_WORDS):
            continue
        j, comp = i, None
        if is_component(w):
            comp, j = w, j + 1
        if j < len(rest) and rest[j] in GROUP_WORDS:
            j += 1
        if j == i or j >= len(rest):
            continue
        return i, j + 1, comp, rest[j]
    return None


def _match_group(rest, catalog: Catalog, names, mode, strength) -> list[Constraint] | None:
    """Group rules: "prefer CS101 lab 11", "lab group 11 for CS101", "take CS101 group 11".

    The group is named by its component, by "group"/"section"/"sec", or both. Without a component, the
    group number must belong to only one of the course's components. None if no group is mentioned.
    """
    if mode not in ("prefer", "avoid", "take"):
        return None
    known = {comp.lower() for c in catalog.courses for comp in c.components}

    def is_component(w):
        return w in known or COMPONENT_ALIASES.get(w) in known

    ref = _group_reference(rest, is_component)
    if ref is not None:
        start, end, comp, group = ref
        before, after = rest[:start], rest[end:]
        if before and after:
            raise _Unparsed("not_understood")
        course_words = before or after
        if not course_words:
            raise _Unparsed("not_understood")  # "prefer group 11": which course?
    elif len(rest) >= 3 and _is_course(" ".join(rest[:-2]), names):
        course_words, comp, group = rest[:-2], rest[-2], rest[-1]  # a component this catalog doesn't have
    else:
        return None

    name = _resolve(" ".join(course_words), names)
    if name.kind != "course":
        raise _Unparsed("not_understood")
    course = next(c for c in catalog.courses if c.id == name.value)
    by_lower = {k.lower(): k for k in course.components}
    groups_of = {k: [g.group for g in gs] for k, gs in course.components.items()}

    if comp is not None:
        canonical = comp if comp in by_lower else COMPONENT_ALIASES.get(comp)
        if canonical not in by_lower:
            raise _Unparsed("unknown_name", sorted(course.components))
        component = by_lower[canonical]
        if group not in groups_of[component]:
            raise _Unparsed("unknown_name", sorted(groups_of[component]))
    else:
        holders = [k for k, gs in groups_of.items() if group in gs]
        if len(holders) > 1:
            raise _Unparsed("ambiguous_name", sorted(f"{k} {group}" for k in holders))
        if not holders:
            raise _Unparsed("unknown_name", sorted(f"{k} {g}" for k, gs in groups_of.items() for g in gs))
        component = holders[0]

    if mode == "take":  # take the course, and only in this group
        return [MustTake(course=course.id, strength=_apply(strength)),
                PreferGroup(course=course.id, component=component, group=group, strength=_apply(strength))]
    if mode == "prefer":
        return [PreferGroup(course=course.id, component=component, group=group, strength=_apply(strength, "soft"))]
    return [AvoidGroup(course=course.id, component=component, group=group, strength=_apply(strength))]
