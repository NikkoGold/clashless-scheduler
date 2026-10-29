# Brute-force oracle

Implement in `tests/helpers/oracle.py` during build step 1, alongside `invariants.py`. It answers "what *should* the solver return?" by trying every combination, so solver bugs that still produce valid-looking schedules get caught.

## API
- `enumerate_schedules(catalog, constraints) -> list[Schedule]`
  - For every allowed course selection (respecting `must_take`, `exclude_course`, `take_n_of`), take `itertools.product` over the **merged options** of each component.
  - Keep only combinations that are clash-free (`a.start < b.end and b.start < a.end`) and satisfy every hard constraint, using the dispatch table in `invariants.py`.
- `score(schedule, constraints) -> int` — total weighted soft penalty, computed from the dispatch table's soft-penalty functions, never by calling the solver.
- `assert_matches_oracle(results, catalog, constraints, k)`:
  1. Feasibility agrees: solver says infeasible ⇔ oracle list is empty.
  2. Best score equals the oracle's minimum.
  3. `len(results) == min(k, len(oracle))`.
  4. `sorted(score(r) for r in results) == sorted(oracle scores)[:len(results)]` — compare score multisets, never positions, because tied schedules can come back in any order.

## Size guard
Before enumerating, compute the number of combinations. Above ~5,000, raise `OracleTooLarge` so a test can't silently become slow. Tests on bigger catalogs skip the oracle and rely on hand-written expectations.

## Shared-bug risk
The oracle and the invariant checker share the per-kind dispatch table. If a rule is misunderstood there, solver and oracle can agree on the same wrong answer. That's why the named tests in SKILL.md still state their expected answer by hand.
