# -*- coding: utf-8 -*-
"""The dictation page: a local server on 127.0.0.1.

It does not face the outside world, only this machine. No password, none needed.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as cfg_mod
from . import store

PAGE = Path(__file__).resolve().parent / "web" / "index.html"


class Handler(BaseHTTPRequestHandler):
    fixes = None      # injected at startup so edits can teach the dictionary
    on_terms = None   # called when the term list has changed
    polisher = None   # the corrector: the page shows and switches its model
    # HTTP/1.1 is needed for the Expect: 100-continue header to work: without
    # it, strict clients abort a POST halfway (a browser would swallow it, but
    # then such a server could not be tested).
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console quiet
        pass

    # --- helpers ---
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # --- routes ---
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path

        if path in ("/", "/index.html"):
            if not PAGE.exists():
                return self._send(500, b"index.html not found", "text/plain")
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")

        if path == "/api/records":
            return self._json(
                store.records(
                    limit=int(q.get("limit", ["200"])[0]),
                    query=q.get("q", [""])[0],
                    only_bad=q.get("bad", ["0"])[0] == "1",
                )
            )

        if path == "/api/stats":
            st = store.stats()
            # A page version stamp: if the file changed, an open tab reloads
            # itself. Otherwise it silently stays on the old code and "dies"
            # when the server changes underneath it.
            try:
                st["page_version"] = int(PAGE.stat().st_mtime)
            except OSError:
                st["page_version"] = 0
            return self._json(st)

        if path == "/api/audio":
            p = store.audio_path(q.get("id", [""])[0])
            if not p:
                return self._send(404, b"no audio", "text/plain")
            return self._send(200, p.read_bytes(), "audio/wav")

        if path == "/api/models":
            if self.polisher is None:
                return self._json({"available": [], "current": "",
                                   "why": "corrector is not wired in"})
            available, why = self.polisher.list_models()
            return self._json({
                "available": available,
                "current": self.polisher.model if self.polisher.model in available
                           else (available[0] if available else ""),
                "enabled": bool(self.polisher.enabled),
                "why": why,
            })

        if path == "/api/files":
            return self._json(
                {
                    "fixes": store.read_file(cfg_mod.FIXES_PATH),
                    "glossary": store.read_file(cfg_mod.GLOSSARY_PATH),
                    "mywords": store.read_file(cfg_mod.MYWORDS_PATH),
                }
            )

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/mark":
            return self._json(store.set_bad(body.get("id", ""), bool(body.get("bad"))))

        if path == "/api/text":
            res = store.set_text(body.get("id", ""), body.get("text", ""), self.fixes)
            if self.fixes is not None and res["learned"]:
                self.fixes.load()
            return self._json(res)

        if path == "/api/term":
            res = store.add_terms(body.get("text", ""))
            if res["added"] and self.on_terms:
                self.on_terms()
            return self._json(res)

        if path == "/api/model":
            name = (body.get("model") or "").strip()
            if self.polisher is None or not self.polisher.use_model(name):
                return self._json({"ok": False, "model": name}, 400)
            # Persist to the settings, or the choice is lost on restart.
            saved = cfg_mod.set_polish_model(name)
            return self._json({"ok": True, "model": name, "saved": saved})

        if path == "/api/delete":
            return self._json(store.delete(body.get("id", "")))

        if path == "/api/files":
            which = body.get("which", "")
            text = body.get("text", "")
            target = {
                "fixes": cfg_mod.FIXES_PATH,
                "glossary": cfg_mod.GLOSSARY_PATH,
                "mywords": cfg_mod.MYWORDS_PATH,
            }.get(which)
            if not target:
                return self._json({"error": "unknown file"}, 400)
            store.write_file(target, text)
            if which == "fixes" and self.fixes is not None:
                self.fixes.load()
            if which in ("glossary", "mywords") and self.on_terms:
                self.on_terms()
            return self._json({"ok": True, "which": which})

        return self._send(404, b"not found", "text/plain")


def start(port: int = 8756, fixes=None, on_terms=None, polisher=None) -> str:
    """Starts the page on its own thread. Returns the address."""
    Handler.fixes = fixes
    Handler.on_terms = staticmethod(on_terms) if on_terms else None
    Handler.polisher = polisher
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/"
