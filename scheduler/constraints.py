"""Constraint types and how each maps into the solver.

Penalties are whole numbers scaled by how badly a rule is broken: hours for time rules, credits
for credit rules (both rounded up), and a count for everything else. Each one is multiplied by
the constraint's weight in the objective. `evaluate` computes the same numbers from a finished
schedule for the score breakdown.
"""
from dataclasses import dataclass, field
from functools import cached_property

from ortools.sat.python import cp_model

from scheduler.models import (
    Catalog,
    Choice,
    Constraint,
    Course,
    Group,
    MustTake,
    Option,
    Session,
    TiedAlternative,
    format_time,
)

CREDIT_SCALE = 100  # credits may be fractional and CP-SAT only handles integers
# A course over a soft take_n_of costs this much, against 1 for a course short: dropping
# a course is easier on a student than adding one, so "one fewer" should rank first.
POOL_OVER_PENALTY = 2
MEMBER_KINDS = {"prefer_group", "avoid_group", "prefer_instructor", "avoid_instructor"}
MAX_PENALTY_HOURS = 24 * 7


def default_constraints(catalog: Catalog) -> list[Constraint]:
    """A hard must_take for each course marked `required` — the starting point for the UI and CLI."""
    return [MustTake(course=c.id) for c in catalog.courses if c.required]


def describe(c: Constraint) -> str:
    """A short plain-language name for a constraint, as used in conflict messages."""
    days = f" on {', '.join(c.days)}" if getattr(c, "days", None) else ""
    match c.kind:
        case "must_take":
            return f"must take {c.course}"
        case "exclude_course":
            return f"exclude {c.course}"
        case "take_n_of":
            return f"take {c.n} of {', '.join(c.courses)}"
        case "no_class_before":
            return f"no class before {format_time(c.time)}{days}"
        case "no_class_after":
            return f"no class after {format_time(c.time)}{days}"
        case "day_off":
            return f"{c.day} off"
        case "max_days":
            return f"at most {c.n} days"
        case "max_hours_per_day":
            return f"at most {c.h} hours a day"
        case "free_block":
            when = f"{c.day} " if c.day else ""
            return f"keep {when}{format_time(c.start)}-{format_time(c.end)} free"
        case "max_gap":
            return f"gaps of at most {c.minutes} minutes"
        case "compact":
            return "compact schedule"
        case "prefer_group":
            return f"prefer {c.course} {c.component} group {c.group}"
        case "avoid_group":
            return f"avoid {c.course} {c.component} group {c.group}"
        case "prefer_instructor":
            return f"prefer {c.name}"
        case "avoid_instructor":
            return f"avoid {c.name}"
        case "min_credits":
            return f"at least {c.n:g} credits"
        case "max_credits":
            return f"at most {c.n:g} credits"
    raise ValueError(f"unknown constraint kind {c.kind!r}")


def check_references(catalog: Catalog, constraints: list[Constraint]) -> None:
    """Raise ValueError if a constraint names something the catalog doesn't have, or a pool is too small."""
    courses = {c.id: c for c in catalog.courses}
    instructors = {g.instructor for c in catalog.courses for gs in c.components.values() for g in gs}

    def known_course(kind: str, course_id: str) -> None:
        if course_id not in courses:
            raise ValueError(f"{kind}: unknown course {course_id!r}")

    for c in constraints:
        if c.kind in ("must_take", "exclude_course"):
            known_course(c.kind, c.course)
        elif c.kind == "take_n_of":
            for course_id in c.courses:
                known_course(c.kind, course_id)
            if len(c.courses) < c.n:
                raise ValueError(f"take_n_of: can't take {c.n} from a pool of {len(c.courses)} ({', '.join(c.courses)})")
        elif c.kind in ("prefer_group", "avoid_group"):
            known_course(c.kind, c.course)
            components = courses[c.course].components
            if c.component not in components:
                raise ValueError(f"{c.kind}: {c.course} has no component {c.component!r}")
            if c.group not in {g.group for g in components[c.component]}:
                raise ValueError(f"{c.kind}: {c.course} {c.component} has no group {c.group!r}")
        elif c.kind in ("prefer_instructor", "avoid_instructor") and c.name not in instructors:
            raise ValueError(f"{c.kind}: unknown instructor {c.name!r}")


def candidate_courses(constraints: list[Constraint]) -> list[str]:
    """Courses that may be scheduled: those in a must_take or a take_n_of pool, of either strength."""
    found: dict[str, None] = {}
    for c in constraints:
        if c.kind == "must_take":
            found[c.course] = None
        elif c.kind == "take_n_of":
            found.update(dict.fromkeys(c.courses))
    return list(found)


# ---- rules about individual groups, which can tell apart the members of a merged option ----

def _teaches(name: str, course: Course, component: str) -> bool:
    return any(g.instructor == name for g in course.components[component])


def member_penalty(c: Constraint, course: Course, component: str, group: Group) -> int:
    """Unweighted penalty for registering for this group."""
    same_component = (course.id, component) == (getattr(c, "course", None), getattr(c, "component", None))
    match c.kind:
        case "prefer_group":
            return int(same_component and group.group != c.group)
        case "avoid_group":
            return int(same_component and group.group == c.group)
        case "prefer_instructor":
            return int(_teaches(c.name, course, component) and group.instructor != c.name)
        case "avoid_instructor":
            return int(group.instructor == c.name)
    raise ValueError(f"{c.kind!r} isn't a member rule")


def keeps_group(constraints: list[Constraint], course: Course, component: str, group: Group) -> bool:
    """False if a hard member rule rules this group out. A broken hard rule is simply a penalty above 0."""
    return not any(
        member_penalty(c, course, component, group)
        for c in constraints
        if c.strength == "hard" and c.kind in MEMBER_KINDS
    )


@dataclass
class MemberChoice:
    register_for: list[str]
    alternatives: list[TiedAlternative]
    cost: int


def best_members(constraints: list[Constraint], course: Course, component: str, option: Option) -> MemberChoice:
    """Which members of an option to recommend. An option is scored by its cheapest member.

    `register_for` is the members with exactly that member's penalties, so the breakdown is true of
    any of them. Members at the same cost that break different preferences become `alternatives`.
    """
    rules = [c for c in constraints if c.strength == "soft" and c.kind in MEMBER_KINDS]
    penalties = {g.group: [member_penalty(c, course, component, g) for c in rules] for g in option.groups}
    costs = {gid: sum(c.weight * p for c, p in zip(rules, values)) for gid, values in penalties.items()}
    best = min(costs, key=costs.get)

    alternatives = [
        TiedAlternative(
            group=gid,
            breaks=[c for c, alt, rec in zip(rules, values, penalties[best]) if alt > rec],
            instead_of=[c for c, alt, rec in zip(rules, values, penalties[best]) if alt < rec],
        )
        for gid, values in penalties.items()
        if costs[gid] == costs[best] and values != penalties[best]
    ]
    register_for = [gid for gid, values in penalties.items() if values == penalties[best]]
    return MemberChoice(register_for, alternatives, costs[best])


def register_note(register_for: list[str], alternatives: list[TiedAlternative]) -> str | None:
    """"Register for 11, or 12, which breaks X instead of Y". None if there's no trade-off to explain."""
    if not alternatives:
        return None
    clauses = [
        f"{alt.group}, which breaks {' and '.join(map(describe, alt.breaks))} "
        f"instead of {' and '.join(map(describe, alt.instead_of))}"
        for alt in alternatives
    ]
    return f"Register for {' or '.join(register_for)}, or " + ", or ".join(clauses)


# ---- rules that apply session by session ----

SESSION_KINDS = {"no_class_before", "no_class_after", "free_block"}


def _minutes_broken(c: Constraint, s: Session) -> int:
    """How many minutes of this session fall where the rule says there should be no class."""
    if c.kind == "free_block":
        if c.day is not None and s.day != c.day:
            return 0
        return max(0, min(s.end, c.end) - max(s.start, c.start))
    if c.days is not None and s.day not in c.days:
        return 0
    if c.kind == "no_class_before":
        return max(0, min(s.end, c.time) - s.start)
    return max(0, s.end - max(s.start, c.time))  # no_class_after


def _hours_up(minutes: int) -> int:
    return (minutes + 59) // 60


def _credits_up(units: int) -> int:
    return (units + CREDIT_SCALE - 1) // CREDIT_SCALE


def _credit_units(credits: float) -> int:
    return round(credits * CREDIT_SCALE)


# ---- the score breakdown ----

def _gaps(choices: list[Choice]) -> list[int]:
    """Every idle stretch between classes on the same day, in hours."""
    by_day: dict[str, list[Session]] = {}
    for ch in choices:
        for s in ch.option.sessions:
            by_day.setdefault(s.day, []).append(s)
    gaps = []
    for sessions in by_day.values():
        ordered = sorted(sessions, key=lambda s: s.start)
        gaps += [(b.start - a.end) // 60 for a, b in zip(ordered, ordered[1:]) if b.start > a.end]
    return gaps


def evaluate(c: Constraint, choices: list[Choice], catalog: Catalog) -> int:
    """The unweighted penalty of a constraint on a finished schedule. Always 0 for a hard one the solver kept."""
    courses = {x.id: x for x in catalog.courses}
    taken = {ch.course for ch in choices}
    sessions = [s for ch in choices for s in ch.option.sessions]

    if c.kind in MEMBER_KINDS:
        total = 0
        for ch in choices:
            wanted = ch.register_for[0]
            group = next(g for g in ch.option.groups if g.group == wanted)
            total += member_penalty(c, courses[ch.course], ch.component, group)
        return total
    if c.kind in SESSION_KINDS:
        return sum(_hours_up(_minutes_broken(c, s)) for s in sessions)

    hours_by_day: dict[str, int] = {}
    for s in sessions:
        hours_by_day[s.day] = hours_by_day.get(s.day, 0) + (s.end - s.start) // 60
    credit_units = sum(_credit_units(courses[cid].credits) for cid in taken)

    match c.kind:
        case "must_take":
            return int(c.course not in taken)
        case "exclude_course":
            return int(c.course in taken)
        case "take_n_of":
            count = len(taken & set(c.courses))
            return max(0, c.n - count) + POOL_OVER_PENALTY * max(0, count - c.n)
        case "day_off":
            return int(c.day in hours_by_day)
        case "max_days":
            return max(0, len(hours_by_day) - c.n)
        case "max_hours_per_day":
            return sum(max(0, hours - c.h) for hours in hours_by_day.values())
        case "max_gap":
            return sum(max(0, gap - c.minutes // 60) for gap in _gaps(choices))
        case "compact":
            return sum(_gaps(choices))
        case "min_credits":
            return _credits_up(max(0, _credit_units(c.n) - credit_units))
        case "max_credits":
            return _credits_up(max(0, credit_units - _credit_units(c.n)))
    raise ValueError(f"unknown constraint kind {c.kind!r}")


# ---- mapping into CP-SAT ----

@dataclass
class Placed:
    """One merged option in the model."""
    course: str
    component: str
    option: Option
    var: cp_model.IntVar


@dataclass
class SolverState:
    catalog: Catalog
    model: cp_model.CpModel
    taking: dict[str, cp_model.IntVar] = field(default_factory=dict)
    placed: list[Placed] = field(default_factory=list)
    costs: list = field(default_factory=list)  # weighted terms of the objective

    def _on_day(self, day: str) -> list[Placed]:
        return [p for p in self.placed if any(s.day == day for s in p.option.sessions)]

    @cached_property
    def days(self) -> list[str]:
        return [d for d in self.catalog.week if self._on_day(d)]

    @cached_property
    def day_used(self) -> dict:
        """A boolean per day: does any chosen option meet that day?"""
        used = {}
        for day in self.days:
            options = [p.var for p in self._on_day(day)]
            b = self.model.new_bool_var(f"used/{day}")
            for var in options:
                self.model.add_implication(var, b)
            self.model.add(b <= sum(options))
            used[day] = b
        return used

    def hours_on(self, day: str):
        """Linear expression for the hours of class on this day."""
        terms = []
        for p in self._on_day(day):
            hours = sum((s.end - s.start) // 60 for s in p.option.sessions if s.day == day)
            terms.append(hours * p.var)
        return sum(terms)

    @cached_property
    def idle(self) -> dict[str, dict]:
        """Per day, a boolean per hour: is this hour empty, with a class both earlier and later?

        Session times are whole hours, so hour slots describe every possible schedule exactly.
        The booleans are only forced up, never down, which is enough because every rule that
        reads them wants them low.
        """
        result = {}
        for day in self.days:
            sessions = [(s, p.var) for p in self._on_day(day) for s in p.option.sessions if s.day == day]
            first, last = min(s.start for s, _ in sessions) // 60, max(s.end for s, _ in sessions) // 60
            hours = list(range(first, last))
            occupied = {h: sum(v for s, v in sessions if s.start // 60 <= h < s.end // 60) for h in hours}

            before, after = {}, {}
            for i, h in enumerate(hours):
                before[h] = self.model.new_bool_var(f"before/{day}/{h}")
                if i:
                    self.model.add(before[h] >= before[h - 1])
                    self.model.add(before[h] >= occupied[h - 1])
            for i, h in reversed(list(enumerate(hours))):
                after[h] = self.model.new_bool_var(f"after/{day}/{h}")
                if i < len(hours) - 1:
                    self.model.add(after[h] >= after[h + 1])
                    self.model.add(after[h] >= occupied[h + 1])

            idle = {h: self.model.new_bool_var(f"idle/{day}/{h}") for h in hours}
            for h in hours:
                self.model.add(idle[h] >= before[h] + after[h] - occupied[h] - 1)
            result[day] = idle
        return result

    def excess_var(self, name: str, upper: int) -> cp_model.IntVar:
        return self.model.new_int_var(0, upper, name)


def _apply_course_rule(state: SolverState, c: Constraint) -> None:
    taking = state.taking.get(getattr(c, "course", None))
    if taking is None:
        return  # exclude_course on a course nothing else selects
    want = 1 if c.kind == "must_take" else 0
    if c.strength == "hard":
        state.model.add(taking == want)
    elif want:
        state.costs.append(c.weight * (1 - taking))
    else:
        state.costs.append(c.weight * taking)


def _apply_take_n_of(state: SolverState, c: Constraint) -> None:
    count = sum(state.taking[course_id] for course_id in c.courses)
    if c.strength == "hard":
        state.model.add(count == c.n)
    else:
        short = state.excess_var("pool_short", len(c.courses))
        over = state.excess_var("pool_over", len(c.courses))
        state.model.add(short >= c.n - count)
        state.model.add(over >= count - c.n)
        state.costs.append(c.weight * short)
        state.costs.append(c.weight * POOL_OVER_PENALTY * over)


def _apply_session_rule(state: SolverState, c: Constraint) -> None:
    for p in state.placed:
        hours = sum(_hours_up(_minutes_broken(c, s)) for s in p.option.sessions)
        if hours == 0:
            continue
        if c.strength == "hard":
            state.model.add(p.var == 0)
        else:
            state.costs.append(c.weight * hours * p.var)


def _apply_day_off(state: SolverState, c: Constraint) -> None:
    if c.strength == "hard":
        for p in state._on_day(c.day):
            state.model.add(p.var == 0)
    elif c.day in state.day_used:
        state.costs.append(c.weight * state.day_used[c.day])


def _apply_max_days(state: SolverState, c: Constraint) -> None:
    used = sum(state.day_used.values())
    if c.strength == "hard":
        state.model.add(used <= c.n)
    else:
        excess = state.excess_var("excess_days", 7)
        state.model.add(excess >= used - c.n)
        state.costs.append(c.weight * excess)


def _apply_max_hours(state: SolverState, c: Constraint) -> None:
    for day in state.days:
        hours = state.hours_on(day)
        if c.strength == "hard":
            state.model.add(hours <= c.h)
        else:
            excess = state.excess_var(f"excess_hours/{day}", 24)
            state.model.add(excess >= hours - c.h)
            state.costs.append(c.weight * excess)


def _apply_max_gap(state: SolverState, c: Constraint) -> None:
    # Sessions are whole hours, so a gap of g hours breaks a limit of m minutes by g - m//60 hours.
    # That's exactly how many windows of (m//60 + 1) consecutive hours fit inside the gap.
    allowed = c.minutes // 60
    for day, idle in state.idle.items():
        hours = list(idle)
        for i in range(len(hours) - allowed):
            window = sum(idle[h] for h in hours[i : i + allowed + 1])
            if c.strength == "hard":
                state.model.add(window <= allowed)
            else:
                broken = state.model.new_bool_var(f"gap/{day}/{hours[i]}")
                state.model.add(broken >= window - allowed)
                state.costs.append(c.weight * broken)


def _apply_compact(state: SolverState, c: Constraint) -> None:
    state.costs.extend(c.weight * var for idle in state.idle.values() for var in idle.values())


def _apply_credits(state: SolverState, c: Constraint) -> None:
    courses = {x.id: x for x in state.catalog.courses}
    total = sum(_credit_units(courses[cid].credits) * var for cid, var in state.taking.items())
    limit = _credit_units(c.n)
    over_limit = limit - total if c.kind == "min_credits" else total - limit
    if c.strength == "hard":
        state.model.add(over_limit <= 0)
    else:
        most = sum(_credit_units(courses[cid].credits) for cid in state.taking) + limit
        excess = state.excess_var(f"excess_credits/{c.kind}", _credits_up(most))
        state.model.add(CREDIT_SCALE * excess >= over_limit)
        state.costs.append(c.weight * excess)


_APPLY = {
    "must_take": _apply_course_rule,
    "exclude_course": _apply_course_rule,
    "take_n_of": _apply_take_n_of,
    "no_class_before": _apply_session_rule,
    "no_class_after": _apply_session_rule,
    "free_block": _apply_session_rule,
    "day_off": _apply_day_off,
    "max_days": _apply_max_days,
    "max_hours_per_day": _apply_max_hours,
    "max_gap": _apply_max_gap,
    "compact": _apply_compact,
    "min_credits": _apply_credits,
    "max_credits": _apply_credits,
    # member rules are handled when options are created: hard ones remove groups, soft ones set the option's cost
    "prefer_group": lambda state, c: None,
    "avoid_group": lambda state, c: None,
    "prefer_instructor": lambda state, c: None,
    "avoid_instructor": lambda state, c: None,
}


def apply_constraint(state: SolverState, c: Constraint) -> None:
    _APPLY[c.kind](state, c)
