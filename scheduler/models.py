"""Catalog and Constraint schemas (Pydantic), plus the solver's result types.

These are the single source of truth for both parsers and the solver.
"""
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

Day = Literal["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
DEFAULT_WEEK: list[Day] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(text: str) -> int:
    """`"10:00"` -> minutes after midnight (600)."""
    m = _TIME.match(text)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError(f"time {text!r} should look like HH:MM")
    return int(m[1]) * 60 + int(m[2])


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class _Strict(BaseModel):
    # unknown fields (e.g. a typo like "instrutor") are errors, not silently ignored
    model_config = ConfigDict(extra="forbid")


# ---- catalog ----

class Session(_Strict):
    day: Day
    start: int  # minutes after midnight; written "HH:MM" in JSON
    end: int
    room: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse(cls, v):
        return parse_time(v) if isinstance(v, str) else v

    @field_validator("start", "end")
    @classmethod
    def _on_the_hour(cls, v: int) -> int:
        if v % 60:
            raise ValueError(f"time {format_time(v)} is not on the hour")
        return v

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end <= self.start:
            raise ValueError(f"end {format_time(self.end)} must be after start {format_time(self.start)}")
        return self

    @field_serializer("start", "end")
    def _format(self, v: int) -> str:
        return format_time(v)


class Group(_Strict):
    group: str
    instructor: str | None = None
    parent: str | None = None  # stored and shown, not enforced (link_groups is deferred)
    attendance_required: bool = True  # checked but unused in v1; kept for max_overlaps
    sessions: list[Session] = Field(min_length=1)

    @model_validator(mode="after")
    def _sessions_dont_overlap(self):
        for i, a in enumerate(self.sessions):
            for b in self.sessions[i + 1:]:
                if a.day == b.day and a.start < b.end and b.start < a.end:
                    raise ValueError(f"group {self.group}: its own sessions overlap on {a.day}")
        return self


class Course(_Strict):
    id: str
    name: str
    credits: float = Field(ge=0)
    required: bool = False  # data-file form of must_take; see constraints.default_constraints
    components: dict[str, Annotated[list[Group], Field(min_length=1)]] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_groups(self):
        for name, groups in self.components.items():
            ids = [g.group for g in groups]
            dupes = sorted({g for g in ids if ids.count(g) > 1})
            if dupes:
                raise ValueError(f"course {self.id}: duplicate group {', '.join(dupes)} in component {name!r}")
        return self


class Catalog(_Strict):
    term: str
    week: list[Day] = Field(default_factory=lambda: list(DEFAULT_WEEK), min_length=1)
    courses: list[Course]

    @model_validator(mode="after")
    def _check(self):
        ids = [c.id for c in self.courses]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate course id {', '.join(dupes)}")
        for c in self.courses:
            for name, groups in c.components.items():
                for g in groups:
                    for s in g.sessions:
                        if s.day not in self.week:
                            raise ValueError(
                                f"course {c.id} {name} group {g.group}: {s.day} is not in the week ({', '.join(self.week)})"
                            )
        return self


# ---- constraints ----

def _clock_minutes(v):
    minutes = parse_time(v) if isinstance(v, str) else v
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 <= minutes < 24 * 60:
        raise ValueError(f"time {v!r} should look like HH:MM")
    return minutes


# Minutes after midnight, written "HH:MM" in JSON. Unlike session times, these needn't be on the hour.
ClockTime = Annotated[int, BeforeValidator(_clock_minutes), PlainSerializer(format_time, return_type=str)]


class _ConstraintBase(_Strict):
    strength: Literal["hard", "soft"] = "hard"
    weight: int = Field(5, ge=1, le=10)


class MustTake(_ConstraintBase):
    kind: Literal["must_take"] = "must_take"
    course: str


class ExcludeCourse(_ConstraintBase):
    kind: Literal["exclude_course"] = "exclude_course"
    course: str


class TakeNOf(_ConstraintBase):
    """Hard: exactly n from this pool. Soft: n is the target, and missing it costs a penalty.

    Going over costs more than going under: see POOL_OVER_PENALTY in constraints.py.
    Only courses in the pool count, so courses selected outside it don't affect n.
    """
    kind: Literal["take_n_of"] = "take_n_of"
    courses: list[str] = Field(min_length=1)
    n: int = Field(ge=1)

    @field_validator("courses")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("take_n_of: a course appears twice in the pool")
        return v


class NoClassBefore(_ConstraintBase):
    kind: Literal["no_class_before"] = "no_class_before"
    time: ClockTime
    days: list[Day] | None = Field(None, min_length=1)  # None means every day


class NoClassAfter(_ConstraintBase):
    kind: Literal["no_class_after"] = "no_class_after"
    time: ClockTime
    days: list[Day] | None = Field(None, min_length=1)  # None means every day


class DayOff(_ConstraintBase):
    kind: Literal["day_off"] = "day_off"
    day: Day


class MaxDays(_ConstraintBase):
    kind: Literal["max_days"] = "max_days"
    n: int = Field(ge=1, le=7)


class MaxHoursPerDay(_ConstraintBase):
    kind: Literal["max_hours_per_day"] = "max_hours_per_day"
    h: int = Field(ge=1)


class FreeBlock(_ConstraintBase):
    kind: Literal["free_block"] = "free_block"
    day: Day | None = None  # None means every day
    start: ClockTime
    end: ClockTime

    @model_validator(mode="after")
    def _start_before_end(self):
        if self.start >= self.end:
            raise ValueError(f"free_block: start {format_time(self.start)} must be before end {format_time(self.end)}")
        return self


class MaxGap(_ConstraintBase):
    kind: Literal["max_gap"] = "max_gap"
    minutes: int = Field(ge=0)


class Compact(_ConstraintBase):
    """Minimise idle time. Soft only: the hard version of this is max_gap(0)."""
    kind: Literal["compact"] = "compact"
    strength: Literal["hard", "soft"] = "soft"

    @field_validator("strength")
    @classmethod
    def _soft_only(cls, v: str) -> str:
        if v != "soft":
            raise ValueError("compact can only be soft; use max_gap with 0 minutes for a hard rule")
        return v


class PreferGroup(_ConstraintBase):
    kind: Literal["prefer_group"] = "prefer_group"
    course: str
    component: str
    group: str


class AvoidGroup(_ConstraintBase):
    kind: Literal["avoid_group"] = "avoid_group"
    course: str
    component: str
    group: str


class PreferInstructor(_ConstraintBase):
    """Applies per component: only components this instructor teaches a group of are affected."""
    kind: Literal["prefer_instructor"] = "prefer_instructor"
    name: str


class AvoidInstructor(_ConstraintBase):
    kind: Literal["avoid_instructor"] = "avoid_instructor"
    name: str


class MinCredits(_ConstraintBase):
    kind: Literal["min_credits"] = "min_credits"
    n: float = Field(ge=0)


class MaxCredits(_ConstraintBase):
    kind: Literal["max_credits"] = "max_credits"
    n: float = Field(ge=0)


Constraint = Annotated[
    MustTake | ExcludeCourse | TakeNOf
    | NoClassBefore | NoClassAfter | DayOff | MaxDays | MaxHoursPerDay | FreeBlock | MaxGap | Compact
    | PreferGroup | AvoidGroup | PreferInstructor | AvoidInstructor
    | MinCredits | MaxCredits,
    Field(discriminator="kind"),
]

_constraint_adapter = TypeAdapter(Constraint)


def parse_constraint(data: dict) -> Constraint:
    return _constraint_adapter.validate_python(data)


# ---- solver results ----

class Option(BaseModel):
    """One choice within a component: a group, or several groups that meet at identical times."""
    groups: list[Group]

    @property
    def group_ids(self) -> tuple[str, ...]:
        return tuple(g.group for g in self.groups)

    @property
    def sessions(self) -> list[Session]:
        return self.groups[0].sessions


class TiedAlternative(BaseModel):
    """Another member of the option at the same cost, which trades one broken preference for another."""
    group: str
    breaks: list[Constraint]  # broken by this group but not by the recommended one
    instead_of: list[Constraint]  # broken by the recommended group but not by this one


class Choice(BaseModel):
    course: str
    component: str
    option: Option
    register_for: list[str]  # the option's best member(s); each gives exactly the schedule's breakdown
    alternatives: list[TiedAlternative] = []
    register_note: str | None = None  # plain-language version of the alternatives, if there are any


class BreakdownItem(BaseModel):
    """How one constraint, hard or soft, fared in a schedule."""
    constraint: Constraint
    met: bool
    penalty: int  # unweighted
    weighted: int


class Schedule(BaseModel):
    choices: list[Choice]
    penalty: int = 0  # total weighted soft penalty; lower is better
    breakdown: list[BreakdownItem] = []  # one entry per constraint, so the user sees what they're accepting


class SolveResult(BaseModel):
    schedules: list[Schedule]  # best first
    note: str | None = None  # e.g. "Only 2 possible schedules match your constraints"
    conflict: list[Constraint] = []  # a minimal set that can't all hold; empty unless nothing was found
