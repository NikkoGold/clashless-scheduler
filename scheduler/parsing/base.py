"""PreferenceParser protocol and ParseResult: the output every parser shares."""
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from scheduler.models import Catalog, Constraint

UnparsedReason = Literal[
    "not_understood",        # no pattern matched the clause (or only part of it did)
    "unknown_name",          # a course, component, group or instructor that isn't in the catalog
    "ambiguous_name",        # several catalog names fit equally well
    "invalid_time",          # an impossible time or range, such as "before 25" or "13-12"
    "conflicting_strength",  # both hard and soft wording in one clause
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnparsedFragment(_Strict):
    """A piece of the user's text that wasn't used, and why, so they know how to fix it."""
    text: str  # exactly as the user wrote it
    reason: UnparsedReason
    candidates: list[str] = []  # suggested names ("did you mean Levi?") or the options to pick between


class Span(_Strict):
    """A fully parsed clause and the constraints it produced, so the UI can show which words made which rule."""
    text: str  # exactly as the user wrote it
    constraints: list[Constraint]


class ParseResult(_Strict):
    constraints: list[Constraint] = []
    unparsed: list[UnparsedFragment] = []
    spans: list[Span] = []


@runtime_checkable
class PreferenceParser(Protocol):
    def parse(self, text: str, catalog: Catalog) -> ParseResult: ...
