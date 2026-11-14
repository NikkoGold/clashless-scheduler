"""A stand-in LLM provider: returns queued replies in order and records every call. Never touches the network.

    fake = FakeProvider([{"constraints": [...], "unparsed": []}, RuntimeError("503")])
    LLMParser(fake).parse(text, cat)
    fake.calls[0]["user"]  # what was sent
"""
import json


class FakeProvider:
    def __init__(self, responses, name="fake", model="fake-1", counts_toward_budget=True):
        self.responses = list(responses)
        self.name = name
        self.model = model
        self.counts_toward_budget = counts_toward_budget
        self.model_version = model
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, schema: dict) -> str:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise AssertionError(f"FakeProvider called {len(self.calls)} times, but only {len(self.calls) - 1} replies were queued")
        reply = self.responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def reply(*constraints, unparsed=()) -> dict:
    """A well-formed model reply: `reply({"kind": "day_off", "day": "Fri"}, unparsed=[("I hate Mondays", "not_understood")])`."""
    return {"constraints": list(constraints), "unparsed": [{"text": t, "reason": r} for t, r in unparsed]}
