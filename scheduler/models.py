"""Catalog and Constraint schemas (Pydantic), plus the solver's result types.

These are the single source of truth for both parsers and the solver.
"""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

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

class MustTake(_Strict):
    kind: Literal["must_take"] = "must_take"
    course: str
    strength: Literal["hard", "soft"] = "hard"
    weight: int = Field(5, ge=1, le=10)


# Becomes a discriminated union on `kind` as step 2 adds constraint kinds.
Constraint = MustTake


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


class Choice(BaseModel):
    course: str
    component: str
    option: Option


class Schedule(BaseModel):
    choices: list[Choice]


class SolveResult(BaseModel):
    schedules: list[Schedule]
    note: str | None = None  # e.g. "Only 2 possible schedules match your constraints"
