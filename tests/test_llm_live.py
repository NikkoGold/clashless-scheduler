"""Live eval: the real LLM parser on the phrases with an `llm:` expectation. The only test that uses the network.

Skipped unless RUN_LIVE_LLM=1. Uses the provider in .env (the project evaluates Gemini), calls the LLMParser directly
(the router would answer simple phrases with the rules) with no cache, and stops after LIVE_LLM_MAX_CALLS real
calls (default 20, retries included). Results vary between runs, so it checks a threshold, not each phrase:
    $env:RUN_LIVE_LLM=1; pytest -m live -s
"""
import os

import pytest

from scheduler.config import load_config
from scheduler.models import parse_constraint
from scheduler.parsing.llm import LLMFailed, LLMParser
from scheduler.parsing.providers import make_provider
from tests.test_rule_parser import CASES, catalog_for

LIVE_CASES = [c for c in CASES if "llm" in c]


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


@pytest.mark.live
def test_the_llm_reads_the_phrase_table_accurately():
    config = load_config()
    if config.provider == "none":
        pytest.skip("no LLM set up in .env")
    provider = make_provider(config)
    cap = CallCap(int(os.environ.get("LIVE_LLM_MAX_CALLS", "20")))
    min_accuracy = float(os.environ.get("LIVE_LLM_MIN_ACCURACY", "0.8"))
    parser = LLMParser(provider, budget=cap)

    rows, passed, attempted = [], 0, 0
    for case in LIVE_CASES:
        if cap.left <= 0:
            rows.append(("skip", case["text"], "", "call limit reached"))
            continue
        attempted += 1
        expected = [parse_constraint(c) for c in case["llm"]["constraints"]]
        try:
            got = parser.parse(case["text"], catalog_for(case)).constraints
            ok = _keys(got) == _keys(expected)
            shown = _short(got)
        except LLMFailed as e:  # a provider error or invalid reply counts as a fail for this phrase
            ok, shown = False, f"failed: {e}"
        passed += ok
        rows.append(("pass" if ok else "FAIL", case["text"], _short(expected), shown))

    print(f"\nLive eval: {config.provider} {config.model} (answered by {provider.model_version}), {cap.used} calls")
    for status, text, expected, got in rows:
        print(f"  {status:4}  {text!r}\n        expected: {expected}\n        got:      {got}")
    accuracy = passed / attempted if attempted else 0.0
    print(f"  {passed}/{attempted} = {accuracy:.0%} (threshold {min_accuracy:.0%})")
    assert attempted, "no phrases were run"
    assert accuracy >= min_accuracy
