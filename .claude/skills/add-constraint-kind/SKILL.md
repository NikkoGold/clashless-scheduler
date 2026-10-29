---
name: add-constraint-kind
description: Checklist for adding a new scheduler constraint kind (rule) or changing an existing one's parameters or meaning — e.g. "add a min_days rule", "support max_classes_per_day", "make free_block take multiple days". Walks the change test-first through the Pydantic model, solver (hard and soft), invariant checker, RuleParser phrases, LLM schema/prompt, UI chip and plan.md so no layer is left untested.
---

# Add or change a constraint kind

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). If `tests/helpers/` doesn't exist yet, set it up via the `test-core-logic` skill first.

Work through the steps **in order**. At the end, report each one as done or "not applicable: <reason>".

## 0. Define it
Write down before touching code:
- name (`snake_case`) and parameters with types/units (times as `HH:MM`, durations in minutes, days `Sun`–`Sat`)
- hard, soft, or both; for soft, how the penalty is measured
- 2–3 example phrases a user would type
- what it means for days not in `week`

## 1. Model
- Test: valid params accepted; invalid params rejected with a clear message (wrong type, out-of-range, bad day, start ≥ end).
- Then add it to the constraint union in `models.py`.

## 2. Invariant checker and oracle
- Add an entry to the dispatch table in `tests/helpers/invariants.py`: `hard_ok` and (if soft is allowed) `soft_penalty`, both computed from the chosen sessions independently of the solver.
- With that entry in place, the brute-force oracle and the Hypothesis strategies cover the new kind; add it to the constraint strategy in `tests/helpers/strategies.py` if that file exists.

## 3. Solver
- Hard test: a tiny catalog where the rule changes the chosen schedule (assert exact groups).
- Infeasible test: the rule conflicts with another hard constraint and the explanation names both.
- Soft test (if soft allowed): ranking changes and the breakdown marks it met/broken with the right weight.
- Each of these also calls `assert_matches_oracle`.
- Then implement in `constraints.py` / `solver.py`.

## 4. RuleParser
- Add entries to `tests/data/phrases.yaml` (see `test-rule-parser`): hard wording ("must", "can't", "never"), soft wording ("prefer", "ideally", "if possible"), and at least one near-miss that must land in `unparsed`.
- Give at least one entry an `llm:` expectation so the live eval covers the new kind.

## 5. LLM parser
- Add the kind to the JSON schema and the prompt's vocabulary.
- Test with the fake provider returning the new kind: it validates and reaches the result.

## 6. UI chip (manual)
- The chip renders, hard/soft toggle and weight edit work, and the "add constraint" form offers it. Note this as a manual check in the summary.

## 7. Docs
- Update the "Constraint vocabulary" section of `plan.md`.

Finish with `pytest -q` green.
