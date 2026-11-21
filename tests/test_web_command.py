"""`python -m scheduler web`: serves on 127.0.0.1 only, takes the next free port, and opens the browser.

Neither uvicorn nor the browser really starts: the test records what they would have been given.
"""
import socket

import pytest

import scheduler.web.server as server
from tests.helpers.cli_runner import cli


class Recorder:
    def __init__(self):
        self.servers, self.opened = [], []

    def run(self, app, host, port, **kw):
        self.servers.append({"app": app, "host": host, "port": port})

    def open(self, url):
        self.opened.append(url)


class NowTimer:
    """threading.Timer that runs at once, so the browser call happens inside the test."""

    def __init__(self, delay, fn, args=()):
        self.fn, self.args = fn, args
        self.daemon = False

    def start(self):
        self.fn(*self.args)


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(server.uvicorn, "run", r.run)
    monkeypatch.setattr(server.webbrowser, "open", r.open)
    monkeypatch.setattr(server.threading, "Timer", NowTimer)
    return r


def held_port() -> socket.socket:
    """A socket listening on a free port, so that port is busy."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def test_it_serves_on_localhost_only(rec, monkeypatch):
    with held_port() as s:  # find a free base port, then release it
        base = s.getsockname()[1]
    monkeypatch.setattr(server, "DEFAULT_PORT", base)
    run = cli("web", "--no-browser")
    assert run.code == 0
    assert rec.servers == [{"app": rec.servers[0]["app"], "host": "127.0.0.1", "port": base}]


def test_a_busy_default_port_moves_to_the_next_and_says_so(rec, monkeypatch):
    with held_port() as s:
        busy = s.getsockname()[1]
        monkeypatch.setattr(server, "DEFAULT_PORT", busy)
        run = cli("web", "--no-browser")
    assert rec.servers[0]["port"] > busy
    assert f"{busy} is in use" in run.out and f"http://127.0.0.1:{rec.servers[0]['port']}" in run.out


def test_an_explicit_port_is_used(rec):
    with held_port() as s:
        port = s.getsockname()[1]
    cli("web", "--port", port, "--no-browser")
    assert rec.servers[0]["port"] == port


def test_an_explicit_busy_port_is_an_error(rec):
    with held_port() as s:
        run = cli("web", "--port", s.getsockname()[1], "--no-browser")
    assert run.code == 2 and "in use" in run.err and rec.servers == []


def test_the_browser_opens_at_the_address(rec, monkeypatch):
    with held_port() as s:
        base = s.getsockname()[1]
    monkeypatch.setattr(server, "DEFAULT_PORT", base)
    cli("web")
    assert rec.opened == [f"http://127.0.0.1:{base}/"]


def test_no_browser_opens_nothing(rec):
    cli("web", "--no-browser")
    assert rec.opened == []


def test_free_port_skips_busy_ones():
    with held_port() as a:
        start = a.getsockname()[1]
        assert server.free_port(start) != start
