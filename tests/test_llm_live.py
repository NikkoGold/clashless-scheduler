"""Live eval: the real LLM parser on the phrases with an `llm:` expectation. The only test that uses the network.

Skipped unless RUN_LIVE_LLM=1. Uses the provider in .env (the project evaluates Gemini), calls the LLMParser directly
(the router would answer simple phrases with the rules) with no cache, and stops after LIVE_LLM_MAX_CALLS real
calls (default 20, retries included).

Every phrase is expected to pass, and each failure is a bug to trace to its root cause. Phrases come in two tiers:
- clear: the rule parser reads the whole phrase, so the LLM must too. Always all-or-nothing.
- hard: the rules leave some of it unparsed. This is what the LLM gets in real use. Also expected to pass in full;
  LIVE_LLM_MIN_ACCURACY (default 1.0) can lower the bar for this tier only, e.g. while a known flaky phrase is fixed.
    $env:RUN_LIVE_LLM=1; pytest -m live -s; Remove-Item Env:RUN_LIVE_LLM
Run it only after a change to what the model is sent or which model answers, not with routine test runs:
each run uses up to 20 calls of the free-tier quota.
"""
import os

import pytest

from scheduler.config import load_config
from scheduler.models import parse_constraint
from scheduler.parsing.llm import LLMFailed, LLMParser
from scheduler.parsing.providers import make_provider
from tests.test_rule_parser import CASES, catalog_for

LIVE_CASES = [c for c in CASES if "llm" in c]
DEFAULT_MAX_CALLS = 20
RETRY_ROOM = 3  # calls left over for retries on invalid replies


def _tier(case) -> str:
    return "hard" if case["rules"]["unparsed"] else "clear"


class CallCap:
    def __init__(self, limit: int):
        self.left = limit
        self.used = 0

    def try_spend(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        self.used += 1
        return True


def _keys(constraints) -> set[str]:
    return {parse_constraint(c).model_dump_json() if isinstance(c, dict) else c.model_dump_json() for c in constraints}


def _one(c) -> str:
    params = {k: v for k, v in c.model_dump(mode="json", exclude_defaults=True).items() if k != "kind"}
    return f"{c.kind}({', '.join(f'{k}={v}' for k, v in params.items())})"


def _short(constraints) -> str:
    return "; ".join(sorted(_one(c) for c in constraints)) or "-"


def test_live_phrases_fit_the_call_limit_with_room_for_retries():
    # a phrase skipped at the call limit would go untested without anyone noticing
    assert len(LIVE_CASES) <= DEFAULT_MAX_CALLS - RETRY_ROOM


def test_most_live_phrases_are_ones_the_rules_cannot_read():
    # in real use the LLM only gets text the rules left unparsed; an eval of easy phrases says little about that
    hard = [c for c in LIVE_CASES if _tier(c) == "hard"]
    assert len(hard) * 2 >= len(LIVE_CASES)


@pytest.mark.live
def test_the_llm_reads_every_phrase_in_the_table():
    config = load_config()
    if config.provider == "none":
        pytest.skip("no LLM set up in .env")
    provider = make_provider(config)
    cap = CallCap(int(os.environ.get("LIVE_LLM_MAX_CALLS", str(DEFAULT_MAX_CALLS))))
    min_hard_accuracy = float(os.environ.get("LIVE_LLM_MIN_ACCURACY", "1.0"))
    parser = LLMParser(provider, budget=cap)

    rows, stopped = [], None
    for case in LIVE_CASES:
        tier = _tier(case)
        if cap.left <= 0:
            rows.append(("skip", tier, case["text"], "", "call limit reached"))
            continue
        expected = [parse_constraint(c) for c in case["llm"]["constraints"]]
        try:
            got = parser.parse(case["text"], catalog_for(case)).constraints
            ok = _keys(got) == _keys(expected)
            shown = _short(got)
        except LLMFailed as e:  # a provider error or invalid reply counts as a fail for this phrase
            ok, shown = False, f"failed: {e}"
            if e.provider_error:  # an outage or a wrong model name would fail every phrase: don't spend more calls
                rows.append(("FAIL", tier, case["text"], _short(expected), shown))
                # the user-facing message is cut short; the full SDK error names the quota hit (per minute or
                # per day) and the suggested retry delay
                stopped = f"{e}\n\nFull provider error:\n{type(e.__cause__).__name__}: {e.__cause__}" if e.__cause__ else str(e)
                break
        rows.append(("pass" if ok else "FAIL", tier, case["text"], _short(expected), shown))

    def count(tier, status=None):
        return sum(1 for r in rows if r[1] == tier and (status is None or r[0] == status))

    print(f"\nLive eval: {config.provider} {config.model} (answered by {provider.model_version}), {cap.used} calls")
    for status, tier, text, expected, got in rows:
        print(f"  {status:4}  {tier:5}  {text!r}\n               expected: {expected}\n               got:      {got}")
    ran_hard = count("hard") - count("hard", "skip")
    hard_accuracy = count("hard", "pass") / ran_hard if ran_hard else 0.0
    print(f"  clear: {count('clear', 'pass')}/{count('clear')} (all must pass)")
    print(f"  hard:  {count('hard', 'pass')}/{count('hard')} = {hard_accuracy:.0%} (threshold {min_hard_accuracy:.0%})")

    assert stopped is None, f"stopped at a provider error: {stopped}"
    skipped = [r[2] for r in rows if r[0] == "skip"]
    assert not skipped, f"{len(skipped)} phrases not run (call limit reached; raise LIVE_LLM_MAX_CALLS): {skipped}"
    failed_clear = [r[2] for r in rows if r[1] == "clear" and r[0] == "FAIL"]
    assert not failed_clear, f"phrases the rules can read were misread by the LLM: {failed_clear}"
    assert hard_accuracy >= min_hard_accuracy, [r[2] for r in rows if r[1] == "hard" and r[0] == "FAIL"]
