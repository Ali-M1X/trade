"""The panel's only write path (Ali, 2026-11-15): "I took this signal" / "I skipped it".

A tiny HTTP service on 127.0.0.1, reached through Caddy at /api/ behind the panel's password.
It writes one small JSON file next to the database; the panel build reads it to show the
results of the trades Ali actually took. It never touches the trading databases.

    GET  /api/marks                 -> {id: {"taken": bool, "ts": ms}}
    POST /api/mark {"id", "taken"}  -> the same, after the change (taken null = clear)
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from .panel_insights import ID_RE, marks_path, read_marks

MAX_BODY = 2048
MAX_MARKS = 5000


def apply_mark(cfg: dict, body: dict) -> dict:
    tid, taken = body.get("id"), body.get("taken")
    if not isinstance(tid, str) or not ID_RE.match(tid) or taken not in (True, False, None):
        raise ValueError("bad mark")
    marks = read_marks(cfg)
    if taken is None:
        marks.pop(tid, None)
    else:
        marks[tid] = {"taken": taken, "ts": int(time.time() * 1000)}
    if len(marks) > MAX_MARKS:
        raise ValueError("too many marks")
    path = marks_path(cfg)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return marks


def make_handler(cfg: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "trade-panel-api"

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self) -> str:
            return urlsplit(self.path).path.rstrip("/")

        def do_GET(self):  # noqa: N802
            if self._path() == "/api/marks":
                return self._send(200, read_marks(cfg))
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self._path() != "/api/mark":
                return self._send(404, {"error": "not found"})
            origin, host = self.headers.get("Origin"), self.headers.get("Host")
            if origin and host and urlsplit(origin).netloc != host:   # only the panel page itself
                return self._send(403, {"error": "origin"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    raise ValueError("size")
                body = json.loads(self.rfile.read(n))
                marks = apply_mark(cfg, body if isinstance(body, dict) else {})
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"error": "bad request"})
            self._send(200, marks)

        def log_message(self, fmt, *args):     # journald gets one short line per request
            print(f"panel-api {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8787) -> None:
    HTTPServer((host, port), make_handler(cfg)).serve_forever()   # one request at a time: no write races
