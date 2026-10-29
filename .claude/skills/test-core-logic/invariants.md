# Solver invariants

Implement in `tests/helpers/invariants.py`. Every solver test passes its results through these.

## `assert_valid_schedule(schedule, catalog, constraints)`
Checks one schedule:
1. **Completeness** — every selected course has exactly one option per component; unselected courses have none.
2. **No overlap** — for every pair of chosen sessions on the same day: not (`a.start < b.end and b.start < a.end`). Back-to-back is allowed.
3. **Hard constraints** — every `strength == "hard"` constraint is satisfied, checked independently of the solver (re-derive from the chosen sessions; don't trust solver flags).
4. **Breakdown** — the score breakdown has one entry per soft constraint, each marked met/broken, and the total equals the sum of weighted penalties.

## `assert_valid_results(results, catalog, constraints, k)`
Checks a top-K list:
1. `len(results) <= k`.
2. Each result passes `assert_valid_schedule`.
3. Pairwise distinct, comparing by (course, component, merged option) — two results differing only by members of the same merged option count as identical and must fail.
4. Scores are non-improving by rank (penalty of #i ≤ penalty of #i+1).
5. If `len(results) < k`, the "only N possible" note is present and N == `len(results)`.

Keep the rule definitions in one dispatch table keyed by constraint `kind`. Each entry has a `hard_ok(schedule, c) -> bool` check and a `soft_penalty(schedule, c) -> int` function (unweighted). The invariant checker uses `hard_ok`; the oracle ([oracle.md](oracle.md)) uses both. `add-constraint-kind` then only has to add one entry. An unknown kind must raise, not pass silently.
