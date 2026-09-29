"""Auto-shutdown when the last browser tab closes.

Opt-in via ``ODYSSEUS_AUTO_SHUTDOWN=1`` (the Windows desktop launcher sets it).
Every Odysseus tab reports itself to ``/api/presence`` (open / ping / frozen /
close, see static/js/autoShutdown.js). Once at least one tab has been seen and
none remain for ``ODYSSEUS_AUTO_SHUTDOWN_GRACE`` seconds, the server asks
uvicorn for a graceful stop (same path as Ctrl+C).

Failure direction is deliberately "stay up": a tab the browser froze or put to
sleep is never pruned, only tabs that went silent without saying goodbye for
``ODYSSEUS_AUTO_SHUTDOWN_STALE`` seconds (browser crash, killed process).
"""
import asyncio
import json
import logging
import os
import signal
import time

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

PRESENCE_PATH = "/api/presence"
_EVENTS = {"open", "ping", "frozen", "close"}
_PROXY_FWD_HEADERS = (
    "cf-connecting-ip", "cf-ray", "cf-visitor",
    "x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded",
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def auto_shutdown_enabled() -> bool:
    return (os.getenv("ODYSSEUS_AUTO_SHUTDOWN") or "").strip().lower() in ("1", "true", "yes", "on")


class TabPresence:
    """Tracks open tabs; pure logic so it can be tested without a server."""

    def __init__(self, grace: float = 30.0, stale: float = 1800.0):
        self.grace = grace
        self.stale = stale
        self.tabs: dict[str, dict] = {}
        self.seen_any = False
        self.empty_since: float | None = None

    def record(self, tab: str, event: str, now: float) -> None:
        if event == "close":
            self.tabs.pop(tab, None)
        else:
            self.tabs[tab] = {"last": now, "frozen": event == "frozen"}
            self.seen_any = True
        if self.tabs:
            self.empty_since = None
        elif self.seen_any and self.empty_since is None:
            self.empty_since = now

    def should_shutdown(self, now: float) -> bool:
        for tab, info in list(self.tabs.items()):
            if not info["frozen"] and now - info["last"] > self.stale:
                self.tabs.pop(tab, None)
        if self.tabs or not self.seen_any:
            self.empty_since = None
            return False
        if self.empty_since is None:
            self.empty_since = now
        return now - self.empty_since >= self.grace


def _is_direct_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        return False
    return not any(request.headers.get(h) for h in _PROXY_FWD_HEADERS)


def _request_graceful_exit() -> None:
    # uvicorn installs a SIGINT handler that flips should_exit, so this is the
    # same graceful shutdown as pressing Ctrl+C in the server window.
    signal.raise_signal(signal.SIGINT)


def setup_auto_shutdown_routes() -> APIRouter:
    router = APIRouter()
    enabled = auto_shutdown_enabled()
    presence = TabPresence(
        grace=_env_float("ODYSSEUS_AUTO_SHUTDOWN_GRACE", 30.0),
        stale=_env_float("ODYSSEUS_AUTO_SHUTDOWN_STALE", 600.0),
    )
    state = {"watcher": None}

    async def _watch():
        while True:
            await asyncio.sleep(5)
            if presence.should_shutdown(time.monotonic()):
                logger.info("Auto-shutdown: all Odysseus tabs closed for %.0fs, stopping server", presence.grace)
                _request_graceful_exit()
                return

    @router.post(PRESENCE_PATH)
    async def tab_presence(request: Request):
        if not enabled or not _is_direct_loopback(request):
            return {"enabled": False}
        try:
            data = json.loads((await request.body()) or b"{}")
        except (ValueError, UnicodeDecodeError):
            data = {}
        tab = str(data.get("tab") or "")[:64]
        event = data.get("event")
        if not tab or event not in _EVENTS:
            return {"enabled": True, "ok": False}
        presence.record(tab, event, time.monotonic())
        if state["watcher"] is None:
            state["watcher"] = asyncio.create_task(_watch())
        return {"enabled": True, "ok": True, "tabs": len(presence.tabs)}

    if enabled:
        logger.info("Auto-shutdown enabled: server stops when the last tab closes")
    return router
