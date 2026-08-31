"""Tests for the HTTP layer.

The server is a thin proxy, so these cover the parts that are easy to get
subtly wrong: passing the filters through, keying the cache by them (a cache
keyed on path alone would serve filtered results to an unfiltered request),
and refusing writes when running read-only.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def client(fake_openportal, monkeypatch):
    """A live server on an ephemeral port, with opdata stubbed out."""
    import server

    monkeypatch.setattr(server, "_cache", {}, raising=False)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, payload=None):
        url = f"{base}{path}"
        if payload is None:
            request = urllib.request.Request(url)
        else:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, None

    yield call, server
    httpd.shutdown()


def test_topology_is_served(client, chain):
    call, _ = client
    status, body = call("/api/topology")
    assert status == 200
    assert body["ok"] is True
    assert len(body["nodes"]) == 5


def test_bridge_failure_is_reported_not_raised(client, monkeypatch):
    """The viewer shows a banner, so a failure must arrive as JSON."""
    call, server = client
    monkeypatch.setattr(server, "topology", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    status, body = call("/api/topology")
    assert status == 200
    assert body["ok"] is False
    assert "down" in body["error"]


def test_agent_filters_reach_the_data_layer(client, monkeypatch):
    call, server = client
    seen = {}

    def fake_detail(path, level=None, search=None, **kw):
        seen.update(path=path, level=level, search=search)
        return {"ok": True}

    monkeypatch.setattr(server, "agent_detail", fake_detail)
    call("/api/agent?path=waldur&level=WARN%2B&q=provider")
    assert seen == {"path": "waldur", "level": "WARN+", "search": "provider"}


def test_cache_is_keyed_by_the_filters(client, monkeypatch):
    """Keying on path alone would serve a filtered answer to everyone."""
    call, server = client
    calls = []

    def fake_detail(path, level=None, search=None, **kw):
        calls.append((path, level, search))
        return {"ok": True, "level": level}

    monkeypatch.setattr(server, "agent_detail", fake_detail)
    call("/api/agent?path=a")
    call("/api/agent?path=a&level=ERROR")
    call("/api/agent?path=a")  # served from cache
    assert calls == [("a", None, None), ("a", "ERROR", None)]


def test_config_reports_the_write_posture(client):
    call, _ = client
    status, body = call("/api/config")
    assert status == 200
    assert body["readonly"] is False


def test_config_lists_the_deployments(client):
    """The page needs the names to label nodes and to address calls."""
    call, _ = client
    _status, body = call("/api/config")
    assert [src["name"] for src in body["sources"]] == ["default"]


def test_the_source_reaches_the_data_layer(client, monkeypatch):
    call, server = client
    seen = []

    def fake_detail(path, level=None, search=None, source=None):
        seen.append((path, source))
        return {"ok": True}

    monkeypatch.setattr(server, "agent_detail", fake_detail)
    call("/api/agent?path=a&source=efp")
    assert seen == [("a", "efp")]


def test_cache_is_keyed_by_the_source(client, monkeypatch):
    """Both deployments have an agent called "portal"; one cache entry would
    hand the other deployment's diagnostics back."""
    call, server = client
    seen = []

    def fake_detail(path, level=None, search=None, source=None):
        seen.append(source)
        return {"ok": True, "source": source}

    monkeypatch.setattr(server, "agent_detail", fake_detail)
    call("/api/agent?path=portal&source=rp")
    _status, body = call("/api/agent?path=portal&source=efp")
    call("/api/agent?path=portal&source=rp")  # served from cache
    assert seen == ["rp", "efp"]
    assert body["source"] == "efp"


def test_the_source_reaches_run_and_registration(client, monkeypatch):
    call, server = client
    seen = []
    monkeypatch.setattr(
        server,
        "run_command",
        lambda cmd, timeout, source=None: seen.append(("run", source)) or {"ok": True},
    )
    monkeypatch.setattr(
        server,
        "sync_offering",
        lambda name, source=None: seen.append(("offering", source)) or {"ok": True},
    )
    call("/api/run", {"command": "dest add_project p", "source": "efp"})
    call("/api/run", {"offering": "demo", "source": "rp"})
    assert seen == [("run", "efp"), ("offering", "rp")]


def test_run_submits_the_command(client, fake_openportal):
    call, _ = client
    status, body = call("/api/run", {"command": "dest add_project p"})
    assert status == 200
    assert body["ok"] is True
    assert fake_openportal.runs[0][0] == "dest add_project p"


def test_run_is_refused_when_read_only(client, fake_openportal, monkeypatch):
    call, server = client
    monkeypatch.setattr(
        server,
        "run_command",
        lambda *a, **k: {"ok": False, "error": "signalbox is running read-only"},
    )
    _, body = call("/api/run", {"command": "dest add_project p"})
    assert body["ok"] is False
    assert "read-only" in body["error"]


def test_offering_registration_goes_through_the_same_endpoint(client, fake_openportal):
    call, _ = client
    _, body = call("/api/run", {"offering": "demo"})
    assert body["ok"] is True
    assert body["address"] == "waldur.demo"
    assert fake_openportal.runs == [], "registering must not submit a job"


def test_empty_command_is_rejected(client, fake_openportal):
    call, _ = client
    _status, body = call("/api/run", {"command": ""})
    assert body["ok"] is False


def test_posting_anywhere_else_is_404(client):
    call, _ = client
    status, _body = call("/api/nope", {"command": "x"})
    assert status == 404


def test_offerings_are_read_per_deployment(client, monkeypatch):
    """What the zone on a portal link exists to carry.

    An offering is registered as a virtual agent in the pair's zone, so an
    award only arrives while that registration stands — which makes the list
    the useful thing to show beside the link, and it is per portal.
    """
    call, server = client
    seen = []
    monkeypatch.setattr(
        server, "offerings", lambda source=None: seen.append(source) or [f"demo.{source}"]
    )
    status, body = call("/api/offerings?source=efp")
    assert status == 200
    assert body == ["demo.efp"]
    call("/api/offerings?source=rp")
    call("/api/offerings?source=efp")  # served from cache
    assert seen == ["efp", "rp"]
