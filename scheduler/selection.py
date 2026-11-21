"""Choosing which courses to schedule: each course is required, in the pool, or off, plus how many to pick.

Shared by the CLI (whose flags build the modes) and the web picker (which sends them), so both behave the same.
"""
from dataclasses import dataclass, field

from scheduler.models import Catalog, Constraint, MustTake, TakeNOf

MODES = ("required", "pool", "off")


class SelectionError(ValueError):
    """A choice that can't be scheduled, with a reason to show the user."""


@dataclass
class Selection:
    required: list[str] = field(default_factory=list)
    pool: list[str] = field(default_factory=list)
    n: int = 0  # how many to take from the pool
    constraints: list[Constraint] = field(default_factory=list)  # hard must_take per required course, then the pool

    def summary(self) -> str:
        """"ECP, OLD (required); 2 of HEP, XBI (pool)"."""
        parts = ([f"{', '.join(self.required)} (required)"] if self.required else []) + \
                ([f"{self.n} of {', '.join(self.pool)} (pool)"] if self.pool else [])
        return "; ".join(parts)


def default_modes(catalog: Catalog) -> dict[str, str]:
    """Each course's starting mode: required if the catalog marks it required, otherwise off."""
    return {c.id: "required" if c.required else "off" for c in catalog.courses}


def select_courses(catalog: Catalog, modes: dict[str, str], pick_n: int | None = None) -> Selection:
    """The constraints for these modes. Courses not in `modes` keep their default; the pool keeps catalog order.

    Without `pick_n`, the whole pool is taken (the UI default: pooling 5 courses takes all 5).
    """
    known = {c.id for c in catalog.courses}
    for course_id, mode in modes.items():
        if course_id not in known:
            raise SelectionError(f"unknown course {course_id!r}")
        if mode not in MODES:
            raise SelectionError(f"{course_id}: {mode!r} isn't a mode (use {', '.join(MODES)})")
    chosen = {**default_modes(catalog), **modes}
    required = [c.id for c in catalog.courses if chosen[c.id] == "required"]
    pool = [c.id for c in catalog.courses if chosen[c.id] == "pool"]

    n = len(pool) if pick_n is None else pick_n
    if pool and n < 1:
        raise SelectionError("the number to pick must be at least 1")
    if n > len(pool):
        raise SelectionError(f"can't take {n} from a pool of {len(pool)}")
    if not required and not pool:
        why = ("the catalog marks no course as required" if not any(c.required for c in catalog.courses)
               else "every required course is switched off")
        raise SelectionError(f"no courses to schedule: {why}")
    constraints = [MustTake(course=c) for c in required] + ([TakeNOf(courses=pool, n=n)] if pool else [])
    return Selection(required=required, pool=pool, n=n if pool else 0, constraints=constraints)
