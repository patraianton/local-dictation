# -*- coding: utf-8 -*-
"""A restart must not cut a take: /api/busy says whether one is being recorded
or is still waiting to be pasted, and Dictation.busy_state counts them right.

No microphone, no models: the page server runs on a free port with a fake.

    ..\\.venv\\Scripts\\python.exe test_busy.py
"""
import json
import socket
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import __main__ as M  # noqa: E402
from stt import server  # noqa: E402

bad = 0


def check(ok, what, detail=""):
    global bad
    bad += not ok
    print(f"[{'v' if ok else 'X'}] {what}" + ("" if ok else f"  ({detail})"))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/busy", timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    # --- 1. Dictation.busy_state ---
    d = object.__new__(M.Dictation)
    d._turn_cv = threading.Condition()
    d._turn_next, d._turn_serving = 0, 0
    d.recording = False
    d.recorder = type("R", (), {"_armed": False})()
    check(d.busy_state() == {"recording": False, "processing": 0}, "idle: nothing", d.busy_state())
    d.recording = True
    check(d.busy_state()["recording"], "a key held down is recording")
    d.recording = False
    d.recorder._armed = True
    check(d.busy_state()["recording"], "the tail after release is still recording")
    d.recorder._armed = False
    d._turn_next = 2
    check(d.busy_state()["processing"] == 2, "two takes drawn, none done", d.busy_state())
    d._turn_serving = 2
    check(d.busy_state()["processing"] == 0, "both done")

    # --- 2. the page answers it ---
    state = {"recording": True, "processing": 1}
    port = free_port()
    server.start(port, busy=lambda: dict(state))
    got = get(port)
    check(got.get("recording") is True and got.get("processing") == 1 and got.get("known") is True,
          "/api/busy gives the app's answer", got)
    state.update(recording=False, processing=0)
    got = get(port)
    check(got.get("recording") is False and got.get("processing") == 0, "and follows it", got)

    def broken():
        raise RuntimeError("boom")

    server.Handler.busy = staticmethod(broken)
    got = get(port)
    check(got.get("known") is False and got.get("recording") is False,
          "a failing answer says unknown, never 'busy forever'", got)

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
