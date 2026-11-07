"""CP-SAT model, top-K enumeration, scoring breakdown, conflict explanation."""
import itertools

from ortools.sat.python import cp_model

from scheduler.catalog import merge_options, overlaps
from scheduler.constraints import (
    Placed,
    SolverState,
    apply_constraint,
    best_members,
    candidate_courses,
    check_references,
    describe,
    evaluate,
    keeps_group,
    register_note,
)
from scheduler.models import BreakdownItem, Catalog, Choice, Constraint, Schedule, SolveResult


def solve(catalog: Catalog, constraints: list[Constraint], k: int = 5) -> SolveResult:
    """Up to k distinct clash-free schedules, best first."""
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    check_references(catalog, constraints)
    if not candidate_courses(constraints):
        raise ValueError("no courses to schedule: add a must_take or take_n_of (or mark courses required)")

    state = _build(catalog, constraints)
    if state.costs:
        state.model.minimize(sum(state.costs))

    solver = _solver()
    schedules = []
    while len(schedules) < k:
        status = solver.solve(state.model)
        if status == cp_model.INFEASIBLE:
            break
        if status != cp_model.OPTIMAL:
            # a result not proven best could be ranked wrongly, so don't show it as if it were
            raise RuntimeError(f"solver stopped with status {solver.status_name(status)}")
        chosen = [p for p in state.placed if solver.boolean_value(p.var)]
        schedules.append(_schedule(chosen, catalog, constraints))
        # rule out this exact combination, and nothing else: a larger schedule containing it is still different
        state.model.add_bool_or([~p.var if solver.boolean_value(p.var) else p.var for p in state.placed])

    conflict = _find_conflict(catalog, constraints) if not schedules else []
    return SolveResult(schedules=schedules, note=_note(len(schedules), k, conflict), conflict=conflict)


def _solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1  # deterministic order between runs
    return solver


def _build(catalog: Catalog, constraints: list[Constraint]) -> SolverState:
    model = cp_model.CpModel()
    state = SolverState(catalog=catalog, model=model)
    courses = {c.id: c for c in catalog.courses}

    # selected courses pick exactly one merged option per component; other courses pick none
    for cid in candidate_courses(constraints):
        course = courses[cid]
        state.taking[cid] = model.new_bool_var(f"taking/{cid}")
        options = merge_options(course, keep=lambda name, g: keeps_group(constraints, course, name, g))
        for component, opts in options.items():
            component_vars = []
            for opt in opts:
                var = model.new_bool_var(f"{cid}/{component}/{'+'.join(opt.group_ids)}")
                state.placed.append(Placed(cid, component, opt, var))
                component_vars.append(var)
                cost = best_members(constraints, course, component, opt).cost
                if cost:
                    state.costs.append(cost * var)
            model.add(cp_model.LinearExpr.sum(component_vars) == state.taking[cid])

    if state.taking:
        model.add_bool_or(list(state.taking.values()))  # never an empty schedule

    # no overlap: at most one of any clashing pair
    for a, b in itertools.combinations(state.placed, 2):
        if (a.course, a.component) != (b.course, b.component) and any(
            overlaps(x, y) for x in a.option.sessions for y in b.option.sessions
        ):
            model.add_at_most_one([a.var, b.var])

    for c in constraints:
        apply_constraint(state, c)
    return state


def _schedule(chosen: list[Placed], catalog: Catalog, constraints: list[Constraint]) -> Schedule:
    courses = {c.id: c for c in catalog.courses}
    choices = []
    for p in chosen:
        members = best_members(constraints, courses[p.course], p.component, p.option)
        choices.append(Choice(
            course=p.course,
            component=p.component,
            option=p.option,
            register_for=members.register_for,
            alternatives=members.alternatives,
            register_note=register_note(members.register_for, members.alternatives),
        ))
    breakdown = []
    for c in constraints:  # every rule, hard and soft: the user should see exactly what they're accepting
        penalty = evaluate(c, choices, catalog)
        breakdown.append(BreakdownItem(constraint=c, met=penalty == 0, penalty=penalty, weighted=c.weight * penalty))
    return Schedule(choices=choices, penalty=sum(item.weighted for item in breakdown), breakdown=breakdown)


def _is_feasible(catalog: Catalog, constraints: list[Constraint]) -> bool:
    if not candidate_courses(constraints):
        return True  # nothing is being asked for, so nothing conflicts
    state = _build(catalog, constraints)
    return _solver().solve(state.model) != cp_model.INFEASIBLE


def _find_conflict(catalog: Catalog, constraints: list[Constraint]) -> list[Constraint]:
    """A minimal set of hard constraints that can't all hold: drop each one, and keep it out if nothing changes."""
    kept = list(range(len(constraints)))
    for i, c in enumerate(constraints):
        if c.strength == "hard":
            without = [constraints[j] for j in kept if j != i]
            if not _is_feasible(catalog, without):
                kept.remove(i)
    return [constraints[i] for i in kept if constraints[i].strength == "hard"]


def _note(found: int, k: int, conflict: list[Constraint]) -> str | None:
    if found == 0:
        if len(conflict) == 1:
            return f"No schedule works: {describe(conflict[0])} can't be met."
        if conflict:
            return f"No schedule works: {' + '.join(describe(c) for c in conflict)} conflict."
        return "No schedule matches your constraints: the chosen courses can't fit together."
    if found == 1 < k:
        return "Only 1 possible schedule matches your constraints."
    if found < k:
        return f"Only {found} possible schedules match your constraints."
    return None
