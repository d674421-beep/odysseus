from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import auto_shutdown_routes as mod
from routes.auto_shutdown_routes import TabPresence


def test_no_shutdown_before_any_tab_seen():
    p = TabPresence(grace=30, stale=1800)
    assert not p.should_shutdown(1000)


def test_shutdown_after_last_tab_closes_and_grace_passes():
    p = TabPresence(grace=30, stale=1800)
    p.record("a", "open", 0)
    p.record("b", "open", 1)
    p.record("a", "close", 10)
    assert not p.should_shutdown(100)
    p.record("b", "close", 100)
    assert not p.should_shutdown(129)
    assert p.should_shutdown(130)


def test_reload_within_grace_keeps_server():
    p = TabPresence(grace=30, stale=1800)
    p.record("a", "open", 0)
    p.record("a", "close", 50)
    p.record("a2", "open", 52)
    assert not p.should_shutdown(200)


def test_silent_tab_pruned_but_frozen_tab_kept():
    p = TabPresence(grace=30, stale=1800)
    p.record("crashed", "open", 0)
    p.record("sleeping", "frozen", 0)
    assert not p.should_shutdown(5000)
    assert "crashed" not in p.tabs and "sleeping" in p.tabs
    p.record("sleeping", "close", 5000)
    assert p.should_shutdown(5030)


def _client(monkeypatch, enabled):
    monkeypatch.setenv("ODYSSEUS_AUTO_SHUTDOWN", "1" if enabled else "0")
    app = FastAPI()
    app.include_router(mod.setup_auto_shutdown_routes())
    return TestClient(app, client=("127.0.0.1", 5000))


def test_endpoint_disabled_by_default(monkeypatch):
    c = _client(monkeypatch, enabled=False)
    assert c.post("/api/presence", json={"tab": "a", "event": "open"}).json() == {"enabled": False}


def test_endpoint_rejects_proxied_requests(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    r = c.post("/api/presence", json={"tab": "a", "event": "open"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.json() == {"enabled": False}


def test_endpoint_tracks_tabs(monkeypatch):
    c = _client(monkeypatch, enabled=True)
    assert c.post("/api/presence", json={"tab": "a", "event": "open"}).json()["tabs"] == 1
    assert c.post("/api/presence", json={"tab": "b", "event": "ping"}).json()["tabs"] == 2
    assert c.post("/api/presence", json={"tab": "a", "event": "close"}).json()["tabs"] == 1
    assert c.post("/api/presence", json={"tab": "a", "event": "bogus"}).json()["ok"] is False
