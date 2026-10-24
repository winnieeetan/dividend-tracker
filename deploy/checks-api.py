#!/usr/bin/env python3
"""Shared calendar ticks for the dividend dashboard.

The dashboard is static, so a tick used to live in whichever browser made it.
This keeps them in one file instead, shared by everyone who can reach the site,
which on this host means everyone on the tailnet.

    GET  /api/checks              -> {"checks": {...}, "rev": N}
    GET  /api/checks/stream       -> server-sent events, one per change
    PUT  /api/checks/<counterId>  -> merge {"cal": bool, "rec": bool}, returns the same

    GET  /api/roster              -> {"added": [...], "removed": [...], "rev": N}
    PUT  /api/roster              -> replace it wholesale, returns the same

Counters added or removed in the dashboard belong to the board, not to the
browser that made the change, so the roster lives here beside the ticks.

Writes are per counter, never the whole map, so two people ticking different
rows at the same time cannot overwrite each other. Standard library only.
"""

import datetime as dt
import json
import os
import threading
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE = os.environ.get("CHECKS_STORE", "/data/checks.json")
ROSTER = os.environ.get("ROSTER_STORE", "/data/roster.json")
EVENTS = os.environ.get("EVENTS_STORE", "/data/events.json")
OVERRIDES = os.environ.get("OVERRIDES_STORE", "/data/overrides.json")
MAX_EVENTS = 2000          # oldest are dropped past this
OWNER = os.environ.get("OWNER_LOGIN", "")
PORT = int(os.environ.get("PORT", "8081"))
MAX_BODY = 64 * 1024
VALID_FIELDS = ("cal", "rec")

_lock = threading.Lock()
_clients = {"n": 0}
# Bumped on every write; waiters wake and push the new state to their browser.
_changed = threading.Condition(_lock)


def _read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(value, fh)
    os.replace(tmp, path)


def _load_roster():
    try:
        with open(ROSTER) as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("added"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"added": [], "removed": [], "rev": 0}


def _save_roster(state):
    tmp = ROSTER + ".tmp"
    os.makedirs(os.path.dirname(ROSTER), exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, ROSTER)


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

    def actor(self):
        """Who is making this request.

        Tailscale Serve sets these headers on every proxied request and
        overrides anything the client sent, so they cannot be forged from a
        browser. A request that bypassed Serve (the healthcheck, or curl on
        loopback) carries none, and is recorded as "local".
        """
        login = (self.headers.get("Tailscale-User-Login") or "").strip()
        name = (self.headers.get("Tailscale-User-Name") or "").strip()
        if not login:
            return {"login": "", "name": "local"}
        return {"login": login[:200], "name": (name or login)[:200]}

    def is_owner(self):
        return bool(OWNER) and self.actor()["login"].lower() == OWNER.lower()

    def record(self, action, target="", detail=""):
        """Append one entry to the activity log. Never raises: an audit trail
        failing must not take a tick with it."""
        try:
            who = self.actor()
            entry = {
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "login": who["login"], "name": who["name"],
                "action": action, "target": str(target)[:200], "detail": str(detail)[:300],
            }
            log = _read_json(EVENTS, [])
            if not isinstance(log, list):
                log = []
            log.append(entry)
            _write_json(EVENTS, log[-MAX_EVENTS:])
        except Exception:
            pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/api/checks/stream":
            return self._stream()
        if path == "/api/roster":
            with _lock:
                return self._send(200, _load_roster())
        if path == "/api/whoami":
            a = self.actor()
            return self._send(200, {"login": a["login"], "name": a["name"],
                                    "isOwner": self.is_owner()})
        if path == "/api/events":
            # The log names who did what; only the owner may read it.
            if not self.is_owner():
                return self._send(403, {"error": "not permitted"})
            with _lock:
                log = _read_json(EVENTS, [])
            return self._send(200, {"events": list(reversed(log))[:500],
                                    "total": len(log)})
        if path == "/api/overrides":
            with _lock:
                return self._send(200, _read_json(OVERRIDES, {"overrides": {}, "rev": 0}))
        if path != "/api/checks":
            return self._send(404, {"error": "not found"})
        with _lock:
            self._send(200, _load())

    def _stream(self):
        """Hold the connection open and write an event whenever a tick changes.

        A tick made on one device shows up on every other open page in about as
        long as the network takes, instead of waiting for the next poll.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")   # nginx: do not buffer
        self.end_headers()

        with _lock:
            _clients["n"] += 1
            n = _clients["n"]
        peer = self.headers.get("X-Forwarded-For") or self.client_address[0]
        self.log_line("STREAM open  from %s (%d connected)" % (peer, n))

        last = None
        try:
            while True:
                with _changed:
                    # Wake on a write, or every 20s to send a keep-alive so
                    # proxies and laptops-asleep do not silently drop us.
                    _changed.wait(timeout=20)
                    state = _load()
                    rstate = _load_roster()
                stamp = (state.get("rev"), rstate.get("rev"))
                if stamp != last:
                    last = stamp
                    payload = dict(state)
                    payload["roster"] = rstate
                    self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                else:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass   # the page went away; nothing to clean up
        finally:
            with _lock:
                _clients["n"] -= 1
                n = _clients["n"]
            self.log_line("STREAM close from %s (%d connected)" % (peer, n))

    def do_PUT(self):
        if self.path.rstrip("/") == "/api/roster":
            return self._put_roster()
        if self.path.startswith("/api/overrides/"):
            return self._put_override(unquote(self.path[len("/api/overrides/"):]))
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
            _changed.notify_all()      # wake every open stream
        on = entry.get("cal") or entry.get("rec")
        self.record("tick" if on else "untick", counter_id,
                    "calendar" if entry.get("cal") else ("reconciled" if entry.get("rec") else ""))
        self._send(200, state)

    def _put_roster(self):
        """The roster is small and edited by one person at a time, so it is
        replaced wholesale rather than merged per counter like the ticks."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if length <= 0 or length > 2 * 1024 * 1024:
            return self._send(400, {"error": "bad body size"})
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if not isinstance(payload, dict) or not isinstance(payload.get("added"), list) \
           or not isinstance(payload.get("removed"), list):
            return self._send(400, {"error": "expected {added: [], removed: []}"})

        with _lock:
            before = _load_roster()
            state = {
                "added": payload["added"][:500],
                "removed": [str(x)[:200] for x in payload["removed"]][:500],
                "rev": int(_load_roster().get("rev", 0)) + 1,
            }
            _save_roster(state)
            _changed.notify_all()
            prev_add = {c.get("id") for c in (before.get("added") or [])}
            prev_rem = set(before.get("removed") or [])
        for c in state["added"]:
            if c.get("id") not in prev_add:
                self.record("add-counter", c.get("id", ""),
                            "%s %s" % (c.get("ticker", ""), c.get("name", "")))
        for rid in state["removed"]:
            if rid not in prev_rem:
                self.record("remove-counter", rid, "")
        self._send(200, state)

    def _put_override(self, counter_id):
        """One counter's hand-entered values. Shared, like the ticks."""
        if not counter_id or len(counter_id) > 200:
            return self._send(400, {"error": "bad counter id"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length)) if 0 < length <= MAX_BODY else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return self._send(400, {"error": "bad json"})

        fields = {k: payload[k] for k in
                  ("exDate", "paymentDate", "currency", "announced", "estimated",
                   "note", "verified") if k in payload}
        with _lock:
            state = _read_json(OVERRIDES, {"overrides": {}, "rev": 0})
            if not isinstance(state.get("overrides"), dict):
                state = {"overrides": {}, "rev": 0}
            if fields:
                state["overrides"][counter_id] = fields
            else:
                state["overrides"].pop(counter_id, None)
            state["rev"] = int(state.get("rev", 0)) + 1
            _write_json(OVERRIDES, state)
            _changed.notify_all()
        self.record("edit" if fields else "clear-edit", counter_id,
                    ", ".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))[:300])
        self._send(200, state)

    def log_line(self, msg):
        import sys
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), msg))
        sys.stderr.flush()

    # Quiet: writes and stream lifecycle only, nothing per ordinary read.
    def log_message(self, fmt, *args):
        if self.command != "GET":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
