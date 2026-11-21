"""The page's wiring, checked without a browser: it only calls API routes that exist (with the right method), only
loads files that exist, loads nothing from the internet, and never stores the API key. How the page looks and behaves
is covered by the manual checklist (.claude/skills/test-interfaces/SKILL.md).
"""
import re
from pathlib import Path

import pytest

from scheduler.web.app import STATIC, create_app
from tests.helpers.api_client import api

pytestmark = pytest.mark.usefixtures("fresh_settings")

JS = sorted(STATIC.glob("*.js"))
PAGE = STATIC / "index.html"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def routes() -> set[tuple[str, str]]:
    return {(method, r.path) for r in create_app().routes if hasattr(r, "methods") for method in r.methods}


def api_calls() -> list[tuple[str, str]]:
    """Every request("METHOD", "/api/...") in the page's scripts."""
    return [(m, p) for f in JS for m, p in re.findall(r'request\("(GET|POST)", "(/api/[^"]*)"', source(f))]


def test_the_page_has_scripts():
    assert PAGE.is_file() and (STATIC / "app.js").is_file() and len(JS) > 3


def test_every_api_call_is_a_route_with_that_method():
    calls = api_calls()
    assert calls, "found no API calls: has api.js changed shape?"
    missing = [c for c in calls if c not in routes()]
    assert missing == []


def test_only_api_js_talks_to_the_server():
    talkers = [f.name for f in JS if "fetch(" in source(f) or "XMLHttpRequest" in source(f)]
    assert talkers == ["api.js"]


def test_every_api_route_the_page_needs_is_called():
    called = {p for _, p in api_calls()}
    assert {"/api/sample", "/api/catalog", "/api/setup", "/api/parse", "/api/rules", "/api/solve"} <= called


def test_files_the_page_loads_exist_and_are_served():
    c = api()
    refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', source(PAGE))
    assert "/static/app.js" in refs and "/static/style.css" in refs
    for ref in refs:
        assert (STATIC / ref.removeprefix("/static/")).is_file(), ref
        assert c.get(ref).status_code == 200, ref


@pytest.mark.parametrize("script", JS, ids=lambda p: p.name)
def test_every_module_import_exists(script):
    for name in re.findall(r'from "\./([^"]+)"', source(script)):
        assert (STATIC / name).is_file(), f"{script.name} imports missing {name}"


def test_scripts_are_served_as_javascript():
    c = api()
    for f in JS:
        r = c.get(f"/static/{f.name}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], f.name


@pytest.mark.parametrize("path", [PAGE, STATIC / "style.css", *JS], ids=lambda p: p.name)
def test_nothing_is_loaded_from_the_internet(path):
    urls = set(re.findall(r"https?://[^\s\"'`)]+", source(path))) - {SVG_NAMESPACE}
    # links in comments (the icon licence) are allowed; anything else would be a request to another site
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source(path), flags=re.S)
    assert [u for u in urls if u in code and not u.startswith(SVG_NAMESPACE)] == []


def test_the_api_key_is_never_stored_in_the_browser():
    store = source(STATIC / "store.js")
    saved = re.search(r"SAVED = \[(.*?)\];", store, re.S).group(1)
    assert "key" not in saved.lower() and "setup" not in saved.lower()


def test_the_key_field_is_cleared_after_saving_and_is_a_password_field():
    header = source(STATIC / "header.js")
    assert 'type: "password"' in header and 'key.value = ""' in header


def test_icons_are_svg_not_emoji():
    emoji = re.compile("[☀-➿\U0001f300-\U0001faff]")
    for path in [PAGE, *JS]:
        assert not emoji.search(source(path)), path.name
