---
name: test-rule-parser
description: Test-first workflow for the scheduler's offline RuleParser (parsing/rules.py, parsing/base.py) — phrase-to-constraint cases, hard/soft keywords, time/day patterns, fuzzy course and instructor matching, and the "no guessing, return unparsed" rule. Use when starting build step 3, adding phrase coverage, or fixing a phrase the rule parser misreads.
---

# RuleParser tests (build step 3)

Follow [../test-core-logic/conventions.md](../test-core-logic/conventions.md). Use the catalog builder from `tests/helpers/` for the catalog the parser matches names against.

## Case format: `tests/data/phrases.yaml`
All phrase cases live in one YAML file, shared with the live LLM eval (`test-llm-parser`):
```yaml
- text: "no classes before 10"
  rules: {constraints: [{kind: no_class_before, time: "10:00", strength: hard}], unparsed: []}
  llm:   {constraints: [{kind: no_class_before, time: "10:00", strength: hard}]}
  tags: [time]
- text: "I'm not a morning person"
  rules: {constraints: [], unparsed: [{text: "I'm not a morning person", reason: not_understood}]}
  llm:   {constraints: [{kind: no_class_before, time: "10:00", strength: soft}]}
  tags: [time, vague]
- text: "prefer Levy"
  rules: {constraints: [], unparsed: [{text: "prefer Levy", reason: unknown_name, candidates: [Levi]}]}
  tags: [names]
```
- `rules:` is required and checked exactly by `tests/test_rule_parser.py`.
- `llm:` is optional and only used by the live eval. The two parsers are expected to differ: the rule parser leaves vague phrases unparsed on purpose.
- Constraint defaults apply when omitted (strength hard, weight 5). Compare constraints as an unordered collection; compare `unparsed` exactly, in order.
- `unparsed` entries are `{text, reason, candidates}`: `text` is the original clause, trimmed; `reason` is one of `not_understood`, `unknown_name`, `ambiguous_name`, `invalid_time`, `conflicting_strength`; `candidates` (default `[]`) are the suggestions or options shown to the user.
- The test loads the file and parametrizes with `ids=[case["text"] ...]`, so a failure names the phrase.
- Phrases are parsed against the fixed catalog `tests/data/phrase_catalog.json` (a few courses with distinct names and instructors), so fuzzy matching is reproducible. A case with `catalog: sample` is parsed against `data/sample_courses.json` instead: use it for the realistic traps (similar names GVO/GVM, APA/XPA/XBI, HEP/HEW, SHO/SHM, Cohen/Cohn, "II" names, OLD's tutorial 11 + lab 11). The live LLM eval must honour this key too.
- **Don't rely on hand-picked examples for combinations.** Verbs × list forms ("X and Y", "X, Y and Z", "X, Y") × both catalogs are generated in `test_each_verb_applies_to_every_item_of_a_list`; a new verb or list form goes into its tables (`VERBS`, `LIST_FORMS`, `NAMES`), not into one-off cases. Every phrase is also parsed against both catalogs to check it never breaks.
- Validate the YAML's shape when loading it (unknown keys or a missing `rules:` fail loudly).

## Must cover
- **Clause splitting** on `.`, `,`, `;` and "and" — including "and" inside a single rule ("between 12 and 13" must not split).
- **Times**: "before 10", "after 4pm", "after 16:00", "12-13", "noon"; 12-hour vs 24-hour readings.
- **Days**: "fridays", "Fri", "sun", "every day", "except Tue", "weekdays" (the week minus Fri/Sat), "weekends" (Fri–Sat) → resolved with the catalog's `week` (default Sun–Fri).
- **Strength**: "must", "can't", "never" → hard; "prefer", "ideally", "if possible", "avoid" → soft; no keyword → hard; both → `conflicting_strength`.
- **Fuzzy names** via `difflib`: course id, course name ("the algorithms course"), instructor ("Prof. Levi"); plus a near-miss that must **not** match and goes to `unparsed`.
- **No guessing**: a clause matched only partly goes to `unparsed` whole; nothing is invented.
- Empty input → no constraints, no unparsed.
- **Spans**: one per fully parsed clause, original text, in order; together they give exactly `constraints`.
- **Robustness** (Hypothesis): arbitrary text and word salad from the parser's vocabulary never raise, every fragment quotes the input, and every name passes `check_references`.

## Fixing a misread phrase
1. Add the failing entry to `phrases.yaml` first (add an `llm:` expectation too if you know it).
2. Fix the pattern.
3. Run the whole table — pattern changes often break neighbours.
