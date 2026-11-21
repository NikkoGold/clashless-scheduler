"""The web API behind the page: the same parser, router and solver as the CLI, answering only this computer.

The server keeps no per-user state: the page sends the catalog with each request, so tabs never interfere.
Settings are read on every request, so saving setup applies at once. Display text comes from scheduler/present.py,
so the page says exactly what the CLI says. Every error is {"error": "<readable message>"} with a 4xx status.

Only this computer's browser is answered (see plan.md, Web UI): the server listens on 127.0.0.1, refuses other Host
names (DNS rebinding), accepts only JSON POSTs (which other sites can't send without permission), and sends no
CORS headers (so other sites can't read responses).
"""
import json
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException

from scheduler.catalog import CatalogError, parse_catalog
from scheduler.config import DEFAULT_MODELS, KEY_VARS, Config, load_config, write_setup
from scheduler.constraints import describe
from scheduler.models import Catalog, Constraint, parse_constraint
from scheduler.parsing.providers import Provider, make_provider
from scheduler.parsing.router import Router
from scheduler.present import PROVIDER_CHOICES, parser_label, rule, schedule_view, unparsed_message
from scheduler.selection import SelectionError, select_courses
from scheduler.solver import solve

STATIC = Path(__file__).parent / "static"
SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample_courses.json"
LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1)(?::\d+)?", re.IGNORECASE)
TOP_MIN, TOP_MAX = 1, 20


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _validation_text(errors) -> str:
    parts = []
    for err in errors:
        loc = [str(x) for x in err.get("loc", ()) if x != "body"]
        msg = err.get("msg", "invalid").removeprefix("Value error, ")
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid request"


# ---- request bodies ----

class CatalogBody(BaseModel):
    catalog: Any


class ParseBody(BaseModel):
    catalog: Any
    text: str = ""


class RulesBody(BaseModel):
    constraints: list[dict]


class SolveBody(BaseModel):
    catalog: Any
    constraints: list[dict] = []
    modes: dict[str, str] = {}  # course id -> required / pool / off; missing courses keep their default
    pick_n: int | None = None  # how many to take from the pool; None takes all of it
    top: int = Field(5, ge=TOP_MIN, le=TOP_MAX)


class SetupBody(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None  # empty or missing keeps a saved key
    ollama_url: str | None = None


# ---- helpers ----

def _catalog(data) -> Catalog:
    if not isinstance(data, dict):
        raise ApiError(400, "catalog: expected a JSON object with term and courses")
    try:
        return parse_catalog(data)
    except CatalogError as e:
        raise ApiError(400, str(e)) from None


def _rules(raw: list[dict]) -> list[Constraint]:
    found = []
    for i, item in enumerate(raw, 1):
        try:
            found.append(parse_constraint(item))
        except ValidationError as e:
            raise ApiError(400, f"rule {i} ({item.get('kind')}): {_validation_text(e.errors())}") from None
    return found


def _rule_items(constraints: list[Constraint]) -> list[dict]:
    return [{"constraint": c.model_dump(mode="json"), "text": rule(c)} for c in constraints]


def _setup_status(config: Config) -> dict:
    """The settings as the page may see them: never the key itself."""
    return {
        "provider": config.provider,
        "model": config.model,
        "source": str(config.source) if config.source else None,
        "from_environment": list(config.from_environment),
        "has_key": bool(config.api_key),
        "needs_setup": config.needs_setup,
        "problem": config.problem,
        "providers": [{"name": name, "label": label, "default_model": DEFAULT_MODELS.get(name),
                       "needs_key": name in KEY_VARS} for name, label in PROVIDER_CHOICES],
    }


# ---- the app ----

def create_app(provider_factory: Callable[[Config], Provider] = make_provider) -> FastAPI:
    app = FastAPI(title="Clashless", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def only_this_computer(request: Request, call_next):
        if not LOCAL_HOST.fullmatch(request.headers.get("host", "")):
            return _error(403, "This server only answers this computer: open it at http://127.0.0.1 (wrong Host).")
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return _error(415, "Send JSON (Content-Type: application/json).")
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(request, e: ApiError):
        return _error(e.status, e.message)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, e: RequestValidationError):
        return _error(422, _validation_text(e.errors()))

    @app.exception_handler(HTTPException)
    async def http_error(request, e: HTTPException):
        return _error(e.status_code, str(e.detail))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/sample")
    def sample():
        try:
            return json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        except OSError:
            raise ApiError(404, "the sample catalog isn't available in this installation") from None

    @app.post("/api/catalog")
    def catalog(body: CatalogBody):
        cat = _catalog(body.catalog)
        return {
            "term": cat.term,
            "week": cat.week,
            "courses": [{"id": c.id, "name": c.name, "credits": c.credits, "required": c.required,
                         "components": list(c.components),
                         "instructors": list(dict.fromkeys(g.instructor for gs in c.components.values() for g in gs
                                                           if g.instructor))}
                        for c in cat.courses],
        }

    @app.get("/api/setup")
    def setup_status():
        return _setup_status(load_config())

    @app.post("/api/setup")
    def setup(body: SetupBody):
        try:
            report = write_setup(provider=body.provider, model=body.model or None, api_key=body.api_key or None,
                                 ollama_url=body.ollama_url or None)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        return {"message": report.message, "warning": report.warning, "setup": _setup_status(load_config())}

    @app.post("/api/parse")
    def parse(body: ParseBody):
        cat = _catalog(body.catalog)
        config = load_config()
        text = body.text.strip()
        routed = Router(config, provider_factory=provider_factory).parse(text, cat) if text else None
        result = routed.result if routed else None
        constraints = result.constraints if result else []
        spans, used = [], set()
        for span in (result.spans if result else []):
            indexes = []
            for c in span.constraints:
                i = next((i for i, x in enumerate(constraints) if x == c and i not in used), None)
                if i is not None:
                    used.add(i)
                    indexes.append(i)
            spans.append({"text": span.text, "rules": indexes})
        return {
            "path": routed.path if routed else None,
            "path_label": parser_label(routed, config),
            "note": routed.note if routed else config.problem,
            "rules": _rule_items(constraints),
            "unparsed": [{"text": f.text, "reason": f.reason, "candidates": f.candidates, "message": unparsed_message(f)}
                         for f in (result.unparsed if result else [])],
            "spans": spans,
        }

    @app.post("/api/rules")
    def rules(body: RulesBody):
        return {"rules": _rule_items(_rules(body.constraints))}

    @app.post("/api/solve")
    def solve_request(body: SolveBody):
        cat = _catalog(body.catalog)
        parsed = _rules(body.constraints)
        try:
            selection = select_courses(cat, body.modes, body.pick_n)
        except SelectionError as e:
            raise ApiError(400, str(e)) from None
        constraints = selection.constraints + parsed
        try:
            result = solve(cat, constraints, k=body.top)
        except ValueError as e:
            raise ApiError(400, f"can't solve this request: {e}") from None
        best = result.schedules[0] if result.schedules else None
        return {
            "result": result.model_dump(mode="json"),
            "constraints": [c.model_dump(mode="json") for c in constraints],
            "courses": selection.summary(),
            "schedules": [schedule_view(s, best, cat) for s in result.schedules],
            "note": result.note,
            "timed_out": result.timed_out,
            "conflict": [describe(c) for c in result.conflict],
            "nothing_to_schedule": result.nothing_to_schedule,
        }

    return app


app = create_app()
