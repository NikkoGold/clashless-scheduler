---
name: test-core-logic
description: Test-first workflow for the scheduler's core logic — catalog loading/validation, Pydantic models, constraint mapping and the CP-SAT solver (top-K, merged groups, scoring, infeasibility explanation). Use when writing or changing scheduler/models.py, catalog.py, constraints.py or solver.py or their tests, or when starting build step 1 or 2 of plan.md. Also owns the shared test helpers (catalog builder, invariant checker) that the other scheduler test skills rely on.
---

# Core logic tests (build steps 1–2)

Read [conventions.md](conventions.md) first — it defines the test-first loop, layout, markers and helper rules shared by every scheduler test skill. Then read [invariants.md](invariants.md) for the checker every solver test must call.

## First use: create the helpers
If `tests/helpers/` doesn't exist yet, create it before any other test:
- `tests/helpers/catalog_builder.py` — `catalog(*courses, week=None, term="T")`, `course(id, name=None, credits=3, **components)`, `grp(group, *slots, instructor=None, mandatory=True, parent=None, room=None)`. A slot is a string like `"Sun 10-12"` or `"Mon 10:30-12:15"`. Returns a validated `Catalog` model (not raw dict) unless `raw=True`.
- `tests/helpers/invariants.py` — `assert_valid_schedule(...)` and `assert_valid_results(...)` as specified in invariants.md.
- `tests/helpers/oracle.py` — brute-force checker as specified in [oracle.md](oracle.md).
- `tests/conftest.py` — registers markers, exposes the builders as fixtures only if that reads better; plain imports are fine.

Example:
```python
cat = catalog(
    course("CS101", lecture=[grp("1", "Sun 10-12", instructor="Cohen")],
                    lab=[grp("11", "Mon 10-11"), grp("12", "Mon 10-11")]),
)
```

## Required named tests (from plan.md)
Each item below is its own test function with a descriptive name.

Catalog / models (`test_catalog.py`):
- valid catalog loads; missing/invalid fields give a readable error (bad day, end ≤ start, duplicate group id within a component, unknown `parent` is allowed but stored)
- `week` defaults to Sun–Fri; custom `week` is respected
- `mandatory` defaults to true; `mandatory: false` accepted

Solver (`test_solver_basic.py`, `test_solver_constraints.py`, `test_solver_topk.py`, `test_solver_infeasible.py`):
- back-to-back 10–12 then 12–14 is allowed; 10–12 and 11:59–13 clash
- any lab may pair with any lecture (`parent` not enforced)
- `mandatory: false` does not change the solution
- groups with identical sessions merge into one option ("lab 11 or 12"); results never look identical on the grid; a merged option is scored by its best member and the result names which member to register for
- catalog with exactly 2 feasible schedules and K=5 returns exactly 2 plus the "only 2 possible" note
- top-K results are pairwise distinct and scores are non-improving by rank
- `take_n_of` with pool == n takes all; pool < n errors before solving
- each hard constraint kind: a tiny case where it changes the result
- each soft constraint kind: ranking changes and the breakdown records met/broken
- infeasible: explanation names the conflicting hard constraints (e.g. Fridays off + must take a Friday-only course)

## Writing solver tests well
- Keep catalogs small enough to solve by hand (2–4 courses, 1–3 groups per component). Write the expected answer in a comment.
- For hard constraints assert the exact chosen groups when the answer is unique.
- For soft constraints assert on ranking and score breakdown, not group ids, unless the optimum is unique.
- Always pass every result through `assert_valid_results` — even in tests about something else.
- Never assert exact results against `data/sample_courses.json`; use it only for a smoke test that solving succeeds.

## Oracle (from step 1)
Every solver test whose catalog fits the oracle's size guard also calls `assert_matches_oracle(results, catalog, constraints, k)`. See [oracle.md](oracle.md). Keep the hand-written expected answer too — the oracle shares rule definitions with the checker, so it can't catch a misunderstood rule on its own.

## Property tests (end of step 2)
Once every constraint kind and top-K are implemented, add Hypothesis tests following [property-tests.md](property-tests.md). Turn each failure Hypothesis finds into a named regression test before fixing it.
