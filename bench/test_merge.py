# -*- coding: utf-8 -*-
"""Two readings of a long take, merged place by place (03.11.2026).

The model service is faked by a local server, the cloud recognizer and the
local reading by stand-ins: nothing is sent anywhere, nothing is loaded.

    ..\\.venv\\Scripts\\python.exe test_merge.py
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import merge as merge_mod  # noqa: E402
from stt.__main__ import Dictation as App  # noqa: E402
from stt.merge import Merger, apply, marked, parse_choice, places  # noqa: E402

state = {"answers": [], "codes": [], "delay": 0.0, "seen": []}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        state["seen"].append({"path": self.path, "auth": self.headers.get("Authorization"),
                              "prompt": body["messages"][0]["content"][0]["text"]})
        time.sleep(state["delay"])
        code = state["codes"].pop(0) if state["codes"] else 200
        text = state["answers"].pop(0) if state["answers"] else '{"1":"A"}'
        out = json.dumps({"output": {"message": {"content": [{"text": text}]}}}).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
merge_mod.URL = f"http://127.0.0.1:{srv.server_address[1]}/{{region}}/{{model}}/converse"


def make(models=("first-model",), timeout_s=2.0):
    m = Merger({"merge": {"enabled": True, "models": list(models), "timeout_s": timeout_s}})
    m.key = "test-key"
    m.set_terms(["Alpha Tool"])
    return m


def reset(answers=(), codes=(), delay=0.0):
    state.update(answers=list(answers), codes=list(codes), delay=delay, seen=[])


CLOUD = "Принеси синюю папку и ангдиски словарь со стола."
LOCAL = "Принеси синюю папку и английский словарь со стола."

CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("the places where two readings differ, marks and capitals aside")
def _():
    ta, tb, diff = places("Ну, собери отчёт по складу.", "ну собери отчет по складу")
    ta2, tb2, diff2 = places(CLOUD, LOCAL)
    return (diff == [] and len(diff2) == 1
            and marked(ta2, tb2, diff2) == "Принеси синюю папку и [1: ангдиски | английский] словарь со стола."), f"{diff} {diff2}"


@case("an insertion and a deletion are shown as ∅")
def _():
    ta, tb, diff = places("Положи на полку книгу.", "Положи книгу на полку.")
    text = marked(ta, tb, diff)
    return "∅" in text, text


@case("the answer: B takes the local piece, A keeps the cloud's")
def _():
    ta, tb, diff = places(CLOUD, LOCAL)
    return (apply(ta, tb, diff, {"1": "B"}) == LOCAL and apply(ta, tb, diff, {"1": "A"}) == CLOUD), ""


@case("answers in a fence, in lower case, with junk keys: read as far as they make sense")
def _():
    ok = (parse_choice('```json\n{"1": "b", "2":"A"}\n```', 2) == {"1": "B", "2": "A"}
          and parse_choice('{"1":"4"}', 1) is None
          and parse_choice("no idea", 1) is None
          and parse_choice('{"7":"B","1":"B"}', 2) == {"1": "B"})
    return ok, ""


@case("merge: the model is asked with the key, the terms and the marked text")
def _():
    reset(answers=['{"1":"B"}'])
    m = make()
    out = m.merge(CLOUD, LOCAL)
    s = state["seen"][0]
    return (out == LOCAL and s["auth"] == "Bearer test-key" and "Alpha Tool" in s["prompt"]
            and "[1: ангдиски | английский]" in s["prompt"] and m.last["took_local"] == 1), f"{out} {m.last}"


@case("readings that agree: no question asked")
def _():
    reset()
    m = make()
    return m.merge(CLOUD, CLOUD) == CLOUD and state["seen"] == [], ""


@case("a refused model: the next one in the list answers")
def _():
    reset(answers=['', '{"1":"B"}'], codes=[403, 200])
    m = make(models=("refused", "answers"))
    out = m.merge(CLOUD, LOCAL)
    return out == LOCAL and m.last["model"] == "answers" and len(state["seen"]) == 2, f"{m.last}"


@case("every model refused, a slow answer, a muddled answer: the cloud's text")
def _():
    reset(codes=[403])
    a = make().merge(CLOUD, LOCAL)
    reset(delay=1.5)
    t0 = time.perf_counter()
    b = make(timeout_s=0.6).merge(CLOUD, LOCAL)
    slow = time.perf_counter() - t0
    reset(answers=["I think the second one."])
    c = make().merge(CLOUD, LOCAL)
    state["delay"] = 0.0
    return (a, b, c) == (CLOUD, CLOUD, CLOUD) and slow < 1.4, f"{a!r} {b!r} {c!r} {slow:.2f}"


def stand_in(cloud_text=None, cloud_error=None, here=LOCAL):
    cloud = SimpleNamespace(enabled=True, model="scribe_v2", timeout_s=1.0, timeout_per_s=0.0,
                            grace_s=1.0, last_s=0.4, last_error=cloud_error,
                            wanted=lambda secs: True)

    def transcribe(audio, secs):
        if cloud_text is None:
            raise RuntimeError(cloud_error or "boom")
        return cloud_text

    cloud.transcribe = transcribe
    asr = SimpleNamespace(language="ru", last_model="large-v3-turbo", last_tail_cut=None,
                          last_echo=None, last_reread=None, take_language="ru",
                          last_p_en=None, last_lang_s=0.0, _english_at=None)
    merger = make()
    app = SimpleNamespace(cloud=cloud, asr=asr, merger=merger, last_cloud=None,
                          _call_during=lambda secs: "")
    app.transcribe_resilient = lambda audio, speech_s=None: (here, 0.3)
    app._mark_cloud = lambda text, model: App._mark_cloud(app, text, model)
    app._cloud_and_here = lambda audio, speech_s, secs, merging: App._cloud_and_here(app, audio, speech_s, secs, merging)
    return app


AUDIO = np.zeros(16000, dtype=np.float32)


@case("a long take: read both ways, merged, the journal says so")
def _():
    reset(answers=['{"1":"B"}'])
    app = stand_in(cloud_text=CLOUD)
    text, took = App.transcribe_in_cloud(app, AUDIO, 25.0)
    return (text == LOCAL and app.asr.last_model == "cloud scribe_v2 + large-v3-turbo"
            and app.last_cloud["merge"]["took_local"] == 1), f"{text} {app.asr.last_model} {app.last_cloud}"


@case("a take under the merge length: the cloud alone, no question asked")
def _():
    reset()
    app = stand_in(cloud_text=CLOUD)
    text, _ = App.transcribe_in_cloud(app, AUDIO, 10.0)
    return text == CLOUD and state["seen"] == [] and app.asr.last_model == "cloud scribe_v2", f"{text}"


@case("the cloud fails on a long take: the reading here is used, not read twice")
def _():
    reset()
    app = stand_in(cloud_text=None, cloud_error="HTTP 500")
    calls = []
    app.transcribe_resilient = lambda audio, speech_s=None: (calls.append(1) or (LOCAL, 0.3))
    text, _ = App.transcribe_in_cloud(app, AUDIO, 25.0)
    return text == LOCAL and len(calls) == 1 and "error" in app.last_cloud and state["seen"] == [], f"{text} {calls}"


@case("no key: not merged, the cloud alone")
def _():
    reset()
    app = stand_in(cloud_text=CLOUD)
    app.merger.key = ""
    text, _ = App.transcribe_in_cloud(app, AUDIO, 25.0)
    return text == CLOUD and state["seen"] == [], f"{text}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in CASES:
        ok, info = fn()
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
