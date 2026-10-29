# -*- coding: utf-8 -*-
"""An empty LM Studio by day gets the small corrector back from dictation (24.10.2026).

On 24.10.2026 the morning restore left LM Studio running with nothing in memory
(its own `lms load` hung), and every take from 07:35 to 15:37 came out raw.
LM Studio is faked here and so is its command line: nothing real is started.

    ..\\.venv\\Scripts\\python.exe test_autoload.py
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import NO_MODEL, Polisher, in_hours  # noqa: E402

SMALL = "qwen3-4b-instruct-2507"
BIG = "qwen/qwen3-30b-a3b-2507"
state = {"loaded": [], "up": True}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if not state["up"]:
            self.close_connection = True
            return self.send_error(503)
        data = [{"id": m, "type": "llm", "state": "loaded" if m in state["loaded"] else "not-loaded"}
                for m in (SMALL, BIG)]
        body = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {"content": "ок"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def make(hours: str = "00:00-23:59", load_works: bool = True, hangs_until_restart: bool = False):
    """A Polisher whose `lms` calls are recorded instead of run."""
    pol = Polisher({"polish": {"url": f"http://127.0.0.1:{PORT}", "model": SMALL,
                               "autoload": SMALL, "autoload_hours": hours}}, [])
    calls, notes = [], []
    restarted = {"done": False}

    def lms(args, timeout):
        calls.append(args[0])
        if args[0] == "server":
            state["up"] = True
            return True
        if args[0] == "load" and load_works and (restarted["done"] or not hangs_until_restart):
            state["loaded"] = [args[1]]
            return True
        return False

    def restart():
        calls.append("restart")
        restarted["done"] = True

    pol._lms, pol._restart_lmstudio, pol.on_note = lms, restart, notes.append
    return pol, calls, notes


def probe(pol):
    pol.probe_soon()
    for _ in range(100):
        if not pol._probing:
            break
        time.sleep(0.02)


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("empty LM Studio by day: the small model is loaded and the corrector is on")
def _():
    state.update(loaded=[], up=True)
    pol, calls, notes = make()
    probe(pol)
    return pol.available and calls == ["load"] and state["loaded"] == [SMALL], f"{calls} {notes}"


@case("a model somebody loaded is never touched")
def _():
    state.update(loaded=[BIG], up=True)
    pol, calls, _ = make()
    probe(pol)
    return pol.available and pol.model == BIG and calls == [], f"{calls} {pol.model}"


@case("at night nothing is loaded")
def _():
    state.update(loaded=[], up=True)
    pol, calls, _ = make(hours="00:00-00:00")
    probe(pol)
    return not pol.available and calls == [] and pol.reason == NO_MODEL, f"{calls} {pol.reason}"


@case("a stopped LM Studio by day: its server is started and the model loaded (29.10.2026)")
def _():
    state.update(loaded=[], up=False)
    pol, calls, notes = make()
    probe(pol)
    return pol.available and calls == ["server", "load"], f"{calls} {notes} {pol.reason}"


@case("a stopped LM Studio at night is left alone")
def _():
    state.update(loaded=[], up=False)
    pol, calls, _ = make(hours="00:00-00:00")
    probe(pol)
    state["up"] = True
    return not pol.available and calls == [], f"{calls} {pol.reason}"


@case("a hung load: LM Studio is restarted once and the model loads")
def _():
    state.update(loaded=[], up=True)
    pol, calls, notes = make(hangs_until_restart=True)
    probe(pol)
    return pol.available and calls == ["load", "restart", "load"], f"{calls} {notes}"


@case("a load that never works: two tries, then quiet for 15 minutes")
def _():
    state.update(loaded=[], up=True)
    pol, calls, notes = make(load_works=False)
    probe(pol)
    first = list(calls)
    pol._next_check = 0.0
    probe(pol)
    return (first == ["load", "restart", "load"] and calls == first and not pol.available
            and "next try in 15 min" in notes[-1]), f"{calls} {notes}"


@case("autoload empty in the settings: never loads")
def _():
    state.update(loaded=[], up=True)
    pol, calls, _ = make()
    pol.autoload = ""
    probe(pol)
    return calls == [] and not pol.available, f"{calls}"


@case("hours: inside, outside, across midnight")
def _():
    noon = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))
    late = time.mktime((2026, 10, 24, 23, 30, 0, 0, 0, -1))
    early = time.mktime((2026, 10, 24, 7, 39, 0, 0, 0, -1))
    got = (in_hours("07:40-22:50", noon), in_hours("07:40-22:50", late),
           in_hours("07:40-22:50", early), in_hours("23:00-07:35", late), in_hours("", noon))
    return got == (True, False, False, True, False), f"{got}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in CASES:
        ok, info = fn()
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
