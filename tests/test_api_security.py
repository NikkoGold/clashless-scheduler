"""The local server only answers this computer's browser: other websites can't change settings or read results.

- the Host must be localhost or 127.0.0.1 (blocks DNS rebinding, where another site's name points at 127.0.0.1)
- POSTs must be JSON (another site can send a form or plain text without permission, but not JSON)
- no CORS headers, so another site's page can't read a response
"""
import pytest
from fastapi.testclient import TestClient

from scheduler.web.app import create_app
from tests.helpers.api_client import api, sample

pytestmark = pytest.mark.usefixtures("fresh_settings")

ROUTES = [("GET", "/"), ("GET", "/api/sample"), ("GET", "/api/setup"), ("POST", "/api/setup"),
          ("POST", "/api/catalog"), ("POST", "/api/parse"), ("POST", "/api/rules"), ("POST", "/api/solve"),
          ("POST", "/api/export/xlsx"), ("POST", "/api/export/json")]


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8003"])
def test_local_hosts_are_answered(host):
    assert api().get("/api/setup", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("method, route", ROUTES)
@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "192.168.1.5:8000"])
def test_other_hosts_are_refused_on_every_route(method, route, host):
    r = api().request(method, route, headers={"host": host}, json={} if method == "POST" else None)
    assert r.status_code == 403 and "host" in r.json()["error"].lower()


@pytest.mark.parametrize("route", [r for m, r in ROUTES if m == "POST"])
@pytest.mark.parametrize("content_type", ["application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"])
def test_posts_that_arent_json_are_refused(route, content_type):
    r = api().post(route, content=b"provider=none", headers={"content-type": content_type})
    assert r.status_code == 415 and r.json()["error"]


def test_a_refused_setup_post_writes_nothing(fresh_settings):
    api().post("/api/setup", content=b'{"provider": "none"}', headers={"content-type": "text/plain"})
    assert fresh_settings.saved() == ""


def test_no_cors_headers_even_when_another_site_asks():
    c = api()
    headers = {"origin": "https://evil.example"}
    responses = [c.get("/api/setup", headers=headers),
                 c.post("/api/parse", json={"catalog": sample(), "text": "x"}, headers=headers),
                 c.options("/api/setup", headers={**headers, "access-control-request-method": "POST"})]
    for r in responses:
        assert not any(h.lower().startswith("access-control-allow") for h in r.headers)


def test_the_default_test_host_is_refused_too():
    # TestClient's own default Host ("testserver") isn't local: a reminder that the check is real
    assert TestClient(create_app()).get("/api/setup").status_code == 403
