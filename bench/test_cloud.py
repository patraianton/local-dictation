# -*- coding: utf-8 -*-
"""Long takes go to the cloud recognizer; anything wrong and they are read here (31.10.2026).

The cloud service is faked by a local server, the app by a stand-in: nothing is
sent anywhere and no recognizer is loaded.

    ..\\.venv\\Scripts\\python.exe test_cloud.py
"""
import cgi
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cloud as cloud_mod  # noqa: E402
from stt.__main__ import Dictation as App  # noqa: E402
from stt.cloud import Cloud, clean  # noqa: E402

SR = 16000
state = {"reply": (200, {"text": "Ну, собери отчёт по складу"}), "delay": 0.0, "seen": {}}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        form = cgi.FieldStorage(fp=BytesIO(body), headers=self.headers,
                                environ={"REQUEST_METHOD": "POST"})
        state["seen"] = {"key": self.headers.get("xi-api-key"),
                         "model": form.getvalue("model_id"),
                         "terms": form.getlist("keyterms"),
                         "has_file": "file" in form}
        time.sleep(state["delay"])
        code, payload = state["reply"]
        out = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cloud_mod.URL = f"http://127.0.0.1:{srv.server_address[1]}/v1/speech-to-text"


def make_cloud(**over):
    c = Cloud({"cloud": dict({"enabled": True, "min_seconds": 20, "timeout_s": 1.0,
                              "timeout_per_s": 0.0}, **over)})
    c.key = "test-key"
    c.set_terms(["Alpha Tool", "beta-runner", "x" * 60])
    return c


def stand_in(cloud, call=""):
    asr = SimpleNamespace(language="ru", last_model="large-v3", last_tail_cut="x",
                          last_echo="x", last_reread="x", take_language="en",
                          last_p_en=0.9, last_lang_s=0.1, _english_at=None)
    return SimpleNamespace(cloud=cloud, asr=asr, last_cloud=None,
                           _call_during=lambda secs: call)


def secs(n):
    return np.zeros(int(SR * n), dtype=np.float32)


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("fillers and drawn-out vowels are taken out, a final mark put on")
def _():
    got = [clean("Принеси ящик, а-а-а, и, э-э-э, синюю папку"),
           clean("Можешь емууу какой-то простой-- гайд"),
           clean("Hey, I need, uh, the draft"),
           clean("Ну, мм, это да"),
           clean("А-а-а, полей цветы на балконе")]
    want = ["Принеси ящик, и, синюю папку.", "Можешь ему какой-то простой гайд.",
            "Hey, I need, the draft.", "Ну, это да.", "Полей цветы на балконе."]
    return got == want, f"{got}"


@case("a long take goes out with the key, the model and the terms; too long a term is left out")
def _():
    state.update(reply=(200, {"text": "Ну, а-а-а, собери отчёт по складу"}), delay=0.0)
    app = stand_in(make_cloud())
    text = App.transcribe_in_cloud(app, secs(1), 25.0)
    s = state["seen"]
    ok = (text == "Ну, собери отчёт по складу." and s["key"] == "test-key"
          and s["model"] == "scribe_v2" and s["terms"] == ["Alpha Tool", "beta-runner"]
          and s["has_file"])
    return ok, f"{text!r} {s}"


@case("a cloud take resets what the local read left behind")
def _():
    state.update(reply=(200, {"text": "Собери отчёт"}), delay=0.0)
    app = stand_in(make_cloud())
    App.transcribe_in_cloud(app, secs(1), 25.0)
    a = app.asr
    ok = (a.last_model == "cloud scribe_v2" and a.last_tail_cut is None and a.last_echo is None
          and a.take_language == "ru" and a.last_p_en is None and "s" in app.last_cloud)
    return ok, f"{vars(a)} {app.last_cloud}"


@case("an English cloud take opens the English window")
def _():
    state.update(reply=(200, {"text": "Send the draft to the whole team"}), delay=0.0)
    app = stand_in(make_cloud())
    App.transcribe_in_cloud(app, secs(1), 25.0)
    return app.asr._english_at is not None, ""


@case("a short take is read here, nothing is sent")
def _():
    state["seen"] = {}
    app = stand_in(make_cloud())
    return App.transcribe_in_cloud(app, secs(1), 19.9) is None and state["seen"] == {}, ""


@case("a take during a call is read here, nothing is sent")
def _():
    state["seen"] = {}
    app = stand_in(make_cloud(), call="zoom")
    text = App.transcribe_in_cloud(app, secs(1), 60.0)
    return text is None and state["seen"] == {} and app.last_cloud == {"skipped": "call: zoom"}, ""


@case("no key, or turned off: read here")
def _():
    c = make_cloud()
    c.key = ""
    off = make_cloud(enabled=False)
    return (App.transcribe_in_cloud(stand_in(c), secs(1), 60.0) is None
            and App.transcribe_in_cloud(stand_in(off), secs(1), 60.0) is None), ""


@case("a service error: read here, the reason written down")
def _():
    state.update(reply=(500, {"detail": "boom"}), delay=0.0)
    app = stand_in(make_cloud())
    text = App.transcribe_in_cloud(app, secs(1), 25.0)
    return text is None and "HTTP 500" in app.last_cloud["error"], f"{app.last_cloud}"


@case("an empty answer: read here")
def _():
    state.update(reply=(200, {"text": "а-а-а"}), delay=0.0)
    app = stand_in(make_cloud())
    text = App.transcribe_in_cloud(app, secs(1), 25.0)
    return text is None and "empty" in app.last_cloud["error"], f"{app.last_cloud}"


@case("a slow answer: given up after the timeout, read here")
def _():
    state.update(reply=(200, {"text": "поздно"}), delay=2.0)
    app = stand_in(make_cloud())
    t0 = time.perf_counter()
    text = App.transcribe_in_cloud(app, secs(1), 25.0)
    took = time.perf_counter() - t0
    state["delay"] = 0.0
    return text is None and took < 1.8, f"{took:.2f} s {app.last_cloud}"


@case("the key is read from the environment first, then from the file")
def _():
    import os
    import tempfile

    path = Path(tempfile.mkdtemp()) / "k.env"
    path.write_text("OTHER=1\nELEVENLABS_API_KEY=from-file\n", encoding="utf-8")
    old = os.environ.pop("ELEVENLABS_API_KEY", None)
    try:
        from_file = cloud_mod.read_key({"key_file": str(path)})
        os.environ["ELEVENLABS_API_KEY"] = "from-env"
        from_env = cloud_mod.read_key({"key_file": str(path)})
        missing = (os.environ.pop("ELEVENLABS_API_KEY"),
                   cloud_mod.read_key({"key_file": str(path.parent / "none.env")}))[1]
    finally:
        if old is not None:
            os.environ["ELEVENLABS_API_KEY"] = old
    return (from_file, from_env, missing) == ("from-file", "from-env", ""), f"{from_file} {from_env} {missing}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in CASES:
        ok, info = fn()
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
