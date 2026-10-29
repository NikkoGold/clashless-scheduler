# Property tests (Hypothesis)

Add at the **end of build step 2**, once all constraint kinds and top-K work. Not before — random tests against a half-built solver mostly report missing features.

## Setup
- Add `hypothesis` to the dev extras in `pyproject.toml`.
- File: `tests/test_solver_properties.py`, every test marked `slow`.
- Settings profile in `conftest.py`: `max_examples=100` by default, `deadline=None` (CP-SAT time varies). Keep the Hypothesis example database (`.hypothesis/`, gitignored) so failures replay.

## Strategies (`tests/helpers/strategies.py`)
- Catalogs: 1–4 courses, 1–2 components each, 1–3 groups per component, 1–2 sessions per group.
- Times on a 30-minute grid between 08:00 and 20:00, length 1–3 hours, days drawn from the catalog's `week`.
- Deliberately duplicate a group's sessions some of the time so merging is exercised.
- Constraints: 0–3 drawn from the kinds the solver supports, random hard/soft and weight 1–10.
- Keep everything small enough for the oracle's size guard; `assume()` away the rest.

## Properties
For each generated (catalog, constraints, k in 1–5):
- `assert_valid_results(results, catalog, constraints, k)`
- `assert_matches_oracle(results, catalog, constraints, k)`
- Solving twice gives the same scores (determinism).

When Hypothesis finds a failure, copy the shrunk catalog into a named regression test in the relevant `test_solver_*.py` file before fixing.
