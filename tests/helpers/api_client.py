"""Call the web API in-process, the way the page does, and check what comes back.

    c = api()                                   # or api(provider=FakeProvider([...])) for the LLM path
    parsed = parse(c, sample(), "fridays off")
    solved = solve(c, sample(), parsed["rules"], modes={"ZGC": "off"})   # checked by the invariant checker

Settings come from the `fresh_settings` fixture. The client sends Host 127.0.0.1, as a browser on this machine does.
"""
import json

from fastapi.testclient import TestClient

from scheduler.catalog import parse_catalog
from scheduler.models import SolveResult, parse_constraint
from scheduler.web.app import create_app
from tests.helpers.cli_runner import PHRASE_CATALOG, SAMPLE
from tests.helpers.invariants import assert_valid_results

BASE = "http://127.0.0.1:8000"


def api(provider=None) -> TestClient:
    kw = {"provider_factory": (lambda config: provider)} if provider is not None else {}
    return TestClient(create_app(**kw), base_url=BASE)


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def phrase_catalog() -> dict:
    return json.loads(PHRASE_CATALOG.read_text(encoding="utf-8"))


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def parse(c, cat: dict, text: str, offline: bool = False) -> dict:
    return ok(c.post("/api/parse", json={"catalog": cat, "text": text, "offline": offline}))


def rules_of(parsed: dict) -> list[dict]:
    return [r["constraint"] for r in parsed["rules"]]


def solve(c, cat: dict, constraints=(), modes=None, pick_n=None, top=5) -> dict:
    """Solve and run the result through the invariant checker. `constraints` may be parse()'s "rules" or raw dicts."""
    raw = [r["constraint"] if "constraint" in r else r for r in constraints]
    body = {"catalog": cat, "constraints": raw, "modes": modes or {}, "pick_n": pick_n, "top": top}
    j = ok(c.post("/api/solve", json=body))
    result = SolveResult.model_validate(j["result"])
    assert_valid_results(result, parse_catalog(cat), [parse_constraint(x) for x in j["constraints"]], top)
    return j
