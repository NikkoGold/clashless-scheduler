"""Constraint types and how each maps into the solver."""
from scheduler.models import Catalog, Constraint, MustTake


def default_constraints(catalog: Catalog) -> list[Constraint]:
    """A hard must_take for each course marked `required` — the starting point for the UI and CLI."""
    return [MustTake(course=c.id) for c in catalog.courses if c.required]
