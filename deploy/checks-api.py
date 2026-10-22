#!/usr/bin/env python3
"""Shared calendar ticks for the dividend dashboard.

The dashboard is static, so a tick used to live in whichever browser made it.
This keeps them in one file instead, shared by everyone who can reach the site,
which on this host means everyone on the tailnet.

    GET  /api/checks              -> {"checks": {...}, "rev": N}
    PUT  /api/checks/<counterId>  -> merge {"cal": bool, "rec": bool}, returns the same

Writes are per counter, never the whole map, so two people ticking different
rows at the same time cannot overwrite each other. Standard library only.
"""

import json
import os
import threading
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE = os.environ.get("CHECKS_STORE", "/data/checks.json")
PORT = int(os.environ.get("PORT", "8081"))
MAX_BODY = 64 * 1024
VALID_FIELDS = ("cal", "rec")

_lock = threading.Lock()


def _load():
    try:
        with open(STORE) as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("checks"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"checks": {}, "rev": 0}


def _save(state):
    # Write-then-rename so a reader never sees a half-written file.
    tmp = STORE + ".tmp"
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STORE)


class Handler(BaseHTTPRequestHandler):
    server_version = "dividend-checks/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") != "/api/checks":
            return self._send(404, {"error": "not found"})
        with _lock:
            self._send(200, _load())

    def do_PUT(self):
        prefix = "/api/checks/"
        if not self.path.startswith(prefix):
            return self._send(404, {"error": "not found"})
        # Counter ids contain "|", which arrives percent-encoded.
        counter_id = unquote(self.path[len(prefix):])
        if not counter_id or len(counter_id) > 200:
            return self._send(400, {"error": "bad counter id"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if length <= 0 or length > MAX_BODY:
            return self._send(400, {"error": "bad body size"})

        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if not isinstance(payload, dict):
            return self._send(400, {"error": "expected an object"})

        entry = {f: bool(payload.get(f)) for f in VALID_FIELDS}

        with _lock:
            state = _load()
            if not any(entry.values()):
                state["checks"].pop(counter_id, None)   # untick: drop the row
            else:
                state["checks"][counter_id] = entry
            state["rev"] = int(state.get("rev", 0)) + 1
            _save(state)
            self._send(200, state)

    # Quiet: one line per write, nothing per read.
    def log_message(self, fmt, *args):
        if self.command != "GET":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
