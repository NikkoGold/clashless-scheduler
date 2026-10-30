"""CP-SAT model, top-K enumeration, scoring breakdown."""
import itertools

from ortools.sat.python import cp_model

from scheduler.catalog import merge_options, overlaps
from scheduler.models import Catalog, Choice, Constraint, Schedule, SolveResult


def solve(catalog: Catalog, constraints: list[Constraint], k: int = 5) -> SolveResult:
    """Up to k distinct clash-free schedules. Unranked until scoring arrives in step 2."""
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    selected = _selected_courses(catalog, constraints)

    model = cp_model.CpModel()
    # one boolean per merged option; exactly one option per component of each selected course
    options = []  # (course id, component, Option, var)
    courses = {c.id: c for c in catalog.courses}
    for cid in selected:
        for component, opts in merge_options(courses[cid]).items():
            component_vars = []
            for opt in opts:
                var = model.new_bool_var(f"{cid}/{component}/{'+'.join(opt.group_ids)}")
                options.append((cid, component, opt, var))
                component_vars.append(var)
            model.add_exactly_one(component_vars)

    # no overlap: at most one of any clashing pair
    for (c1, p1, o1, v1), (c2, p2, o2, v2) in itertools.combinations(options, 2):
        if (c1, p1) != (c2, p2) and any(overlaps(a, b) for a in o1.sessions for b in o2.sessions):
            model.add_at_most_one([v1, v2])

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1  # deterministic order between runs
    schedules = []
    while len(schedules) < k:
        status = solver.solve(model)
        if status == cp_model.INFEASIBLE:
            break
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"solver stopped with status {solver.status_name(status)}")
        chosen = [(c, p, o, v) for c, p, o, v in options if solver.boolean_value(v)]
        schedules.append(Schedule(choices=[Choice(course=c, component=p, option=o) for c, p, o, _ in chosen]))
        # rule out this exact combination before solving again
        model.add_bool_or([~v for *_, v in chosen])

    return SolveResult(schedules=schedules, note=_note(len(schedules), k))


def _selected_courses(catalog: Catalog, constraints: list[Constraint]) -> list[str]:
    known = {c.id for c in catalog.courses}
    selected = []
    for c in constraints:
        if c.kind != "must_take":
            raise ValueError(f"constraint kind {c.kind!r} is not supported yet")
        if c.strength != "hard":
            raise ValueError("soft must_take is not supported yet")
        if c.course not in known:
            raise ValueError(f"must_take: unknown course {c.course!r}")
        if c.course not in selected:
            selected.append(c.course)
    if not selected:
        raise ValueError("no courses to schedule: add a must_take (or mark courses required)")
    return selected


def _note(found: int, k: int) -> str | None:
    if found == 0:
        return "No schedule matches your constraints."  # step 2 replaces this with the conflict explanation
    if found == 1 < k:
        return "Only 1 possible schedule matches your constraints."
    if found < k:
        return f"Only {found} possible schedules match your constraints."
    return None
