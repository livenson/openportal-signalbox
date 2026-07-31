#!/usr/bin/env python3
"""signalbox — local proxy and static server for the OpenPortal viewer.

The browser cannot talk to the bridge directly: every call must be signed with
the HMAC key from the bridge invite, and op-bridge only listens inside the
compose network. So this sits in between — it holds the invite, signs calls
through the official ``openportal`` module, and serves plain JSON to the page.

Read-only: it exposes health and diagnostics, and never submits a job.

    GET /                 the viewer
    GET /api/topology     agent tree -> nodes + edges
    GET /api/agent?path=  diagnostics for one agent ("" = the bridge itself),
                          narrowed by optional &level= and &q=
    GET /api/config       what this instance allows
    POST /api/run         submit an instruction (disabled by SIGNALBOX_READONLY)
"""

import json
import logging
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from opdata import (READONLY, agent_detail, bridge, run_command, sync_offering,
                    topology)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("signalbox")

HERE = Path(__file__).parent
INVITE = os.getenv("OPENPORTAL_BRIDGE_INVITE", "/inv/bridge-invite.toml")
PORT = int(os.getenv("PORT", "8900"))

# Diagnostics are fetched per agent on demand and cached briefly, so that
# clicking around the graph does not hammer the network with repeat calls.
CACHE_TTL = float(os.getenv("CACHE_TTL", "3"))
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def cached(key, produce):
    """Short-lived memo so clicking around does not re-hit the agents."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    value = produce()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/topology":
            try:
                self._json(cached("topology", topology))
            except Exception as exc:
                logger.warning("topology failed: %s", exc)
                self._json({"ok": False, "error": str(exc)}, 200)
            return

        if parsed.path == "/api/agent":
            query = parse_qs(parsed.query)
            path = (query.get("path") or [""])[0]
            level = (query.get("level") or [""])[0] or None
            search = (query.get("q") or [""])[0] or None
            key = f"agent:{path}:{level}:{search}"
            try:
                self._json(
                    cached(key, lambda: agent_detail(path, level=level, search=search))
                )
            except Exception as exc:
                logger.warning("diagnostics for %r failed: %s", path, exc)
                self._json({"ok": False, "error": str(exc)}, 200)
            return

        if parsed.path == "/api/config":
            self._json({"readonly": READONLY})
            return

        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"ok": False, "error": "invalid JSON"})
            return

        if payload.get("offering"):
            self._json(sync_offering(payload["offering"]))
            return

        command = payload.get("command", "")
        logger.info("run: %s", command)
        try:
            self._json(run_command(command, payload.get("timeout_ms", 30000)))
        except Exception as exc:
            logger.warning("run failed: %s", exc)
            self._json({"ok": False, "error": str(exc)})


def main():
    try:
        bridge()
        logger.info("Loaded bridge invite from %s", INVITE)
    except Exception as exc:
        logger.error("Could not load the bridge invite: %s", exc)
        sys.exit(1)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info("OpenPortal viewer on http://localhost:%s", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
