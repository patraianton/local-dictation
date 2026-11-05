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
        if state.get("trickle"):
            # Byte by byte, each well inside any single wait: only a limit on
            # the whole request stops this.
            for i in range(len(out)):
                self.wfile.write(out[i:i + 1])
                self.wfile.flush()
                time.sleep(state["trickle"])
            return
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


HERE = "Текст, прочитанный здесь."


def stand_in(cloud, call=""):
    asr = SimpleNamespace(language="ru", last_model="large-v3", last_tail_cut="x",
                          last_echo="x", last_reread="x", take_language="en",
                          last_p_en=0.9, last_lang_s=0.1, _english_at=None)
    app = SimpleNamespace(cloud=cloud, asr=asr, last_cloud=None, merger=None,
                          _call_during=lambda secs: call)
    app._mark_cloud = lambda text, model: App._mark_cloud(app, text, model)
    app.transcribe_resilient = lambda audio, speech_s=None: (HERE, 0.05)
    app._cloud_and_here = lambda a, s, secs, merging: App._cloud_and_here(app, a, s, secs, merging)
    return app


def secs(n):
    return np.zeros(int(SR * n), dtype=np.float32)


def cloud_text(app, seconds):
    """The text the cloud path gives, or None when the take is read here."""
    read = App.transcribe_in_cloud(app, secs(1), seconds)
    return None if read is None else read[0]


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
           clean("Ну, мм, это да. Иии всё"),
           clean("А-а-а, полей цветы на балконе")]
    want = ["Принеси ящик, и, синюю папку.", "Можешь ему какой-то простой гайд.",
            "Hey, I need, the draft.", "Ну, это да. И всё.", "Полей цветы на балконе."]
    return got == want, f"{got}"


@case("stammering is taken out; Russian doubled words and hyphenated words stay")
def _():
    got = [clean("Надо его по-по-по-покрасить"),
           clean("Мы, мы, мы, мы пошли в лес"),
           clean("Положи на, на полку"),
           clean("Там, как-как-как ты это сделал"),
           clean("Сначала, типа, -."),
           clean("Чуть-чуть, по-немецки и во-вторых из-за дождя"),
           clean("Тише, тише, сказал он"),
           clean("Налей ку-ку-кофе, как в, как в кафе"),
           clean("Это ху-ху-хорошо, по-польски"),
           clean("Нельзя с-сразу? О-очень, е-мейл и Т-образный"),
           clean("Ка-как ты ска-скажешь, по-полной и по-польски, из-за дождя"),
           clean("Он так-таки пришёл"),
           clean("Вспомни про про эти книги, в 10 10 минут"),
           clean("Оно как, как будто съехало, в 10, 10 местах"),
           clean("Ну, по-покрась дверь по-португальски и по-походному"),
           clean("У нас, э, три окна, э, и этот, Эх, ладно"),
           clean("Ты можешь пол ты можешь полить цветы, а потом полить грядку"),
           clean("Сделай по-по-своему, кое-как"),
           clean("Хм, не нравится мне это. Мх."),
           clean("П, сделай отчёт, а я к тебе приду, в 5 и с утра"),
           clean("Нужна крас-красивая обложка, открой прило-приложение"),
           clean("Пойдём за-за хлебом, ну-ну, да-да, из-за дождя"),
           clean("Иди к-к дому, в-в лес и с-с ним")]
    want = ["Надо его покрасить.", "Мы пошли в лес.", "Положи на полку.",
            "Там, как ты это сделал.", "Сначала, типа.",
            "Чуть-чуть, по-немецки и во-вторых из-за дождя.", "Тише, тише, сказал он.",
            "Налей кофе, как в кафе.", "Это хорошо, по-польски.",
            "Нельзя сразу? Очень, е-мейл и Т-образный.",
            "Как ты скажешь, по-полной и по-польски, из-за дождя.",
            "Он так-таки пришёл.",
            "Вспомни про эти книги, в 10 10 минут.",
            "Оно как будто съехало, в 10, 10 местах.",
            "Ну, покрась дверь по-португальски и по-походному.",
            "У нас, три окна, и этот, Эх, ладно.",
            "Ты можешь полить цветы, а потом полить грядку.",
            "Сделай по-своему, кое-как.",
            "Не нравится мне это.",
            "Сделай отчёт, а я к тебе приду, в 5 и с утра.",
            "Нужна красивая обложка, открой приложение.",
            "Пойдём за хлебом, ну-ну, да-да, из-за дождя.",
            "Иди к дому, в лес и с ним."]
    return got == want, f"{got}"


@case("a long take goes out with the key, the model and the terms; too long a term is left out")
def _():
    state.update(reply=(200, {"text": "Ну, а-а-а, собери отчёт по складу"}), delay=0.0)
    app = stand_in(make_cloud())
    text = cloud_text(app, 25.0)
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
    text = cloud_text(app, 60.0)
    return text is None and state["seen"] == {} and app.last_cloud == {"skipped": "call: zoom"}, ""


@case("no key, or turned off: read here")
def _():
    c = make_cloud()
    c.key = ""
    off = make_cloud(enabled=False)
    return (App.transcribe_in_cloud(stand_in(c), secs(1), 60.0) is None
            and App.transcribe_in_cloud(stand_in(off), secs(1), 60.0) is None), ""


@case("a service error: the reading here, the reason written down")
def _():
    state.update(reply=(500, {"detail": "boom"}), delay=0.0)
    app = stand_in(make_cloud())
    text = cloud_text(app, 25.0)
    return text == HERE and "HTTP 500" in app.last_cloud["error"], f"{text} {app.last_cloud}"


@case("an empty answer: the reading here")
def _():
    state.update(reply=(200, {"text": "а-а-а"}), delay=0.0)
    app = stand_in(make_cloud())
    text = cloud_text(app, 25.0)
    return text == HERE and "empty" in app.last_cloud["error"], f"{text} {app.last_cloud}"


@case("a slow answer: dropped a second after the reading here, which is used")
def _():
    state.update(reply=(200, {"text": "поздно"}), delay=2.0)
    app = stand_in(make_cloud())
    t0 = time.perf_counter()
    text = cloud_text(app, 25.0)
    took = time.perf_counter() - t0
    state["delay"] = 0.0
    return text == HERE and took < 1.5, f"{text} {took:.2f} s {app.last_cloud}"


@case("a long take waits the cloud's usual time past the reading here")
def _():
    state.update(reply=(200, {"text": "Длинный ответ облака"}), delay=1.6)
    c = make_cloud(timeout_s=5.0, grace_s=0.3, grace_per_s=0.03)
    app = stand_in(c)
    text = cloud_text(app, 60.0)        # 0.3 + 0.03 * 60 = 2.1 s from the start
    short = stand_in(make_cloud(timeout_s=5.0, grace_s=0.3, grace_per_s=0.03, min_seconds=5))
    t_short = cloud_text(short, 6.0)    # 0.3 + 0.18 = 0.48 s: too soon, the reading here
    state["delay"] = 0.0
    return text == "Длинный ответ облака." and t_short == HERE, f"{text!r} {t_short!r}"


@case("late on 3 of the last 4 takes: not waited for; on time again: waited for again")
def _():
    c = make_cloud(timeout_s=3.0, grace_s=0.3, min_seconds=5)
    app = stand_in(c)
    state.update(reply=(200, {"text": "Облако"}), delay=0.8)
    late = [cloud_text(app, 6.0) for _ in range(3)]      # each dropped after 0.3 s
    time.sleep(0.8)                                        # the late answers still arrive
    state["delay"] = 0.1
    t0 = time.perf_counter()
    skipped = cloud_text(app, 6.0)                         # would be on time, but not waited for
    took = time.perf_counter() - t0
    time.sleep(0.3)
    back = []
    for _ in range(3):                                     # on time again: the recent ones turn
        back.append(cloud_text(app, 6.0))
        time.sleep(0.2)                                    # takes come seconds apart
    state["delay"] = 0.0
    return (late == [HERE] * 3 and skipped == HERE and took < 0.08
            and back[-1] == "Облако."), f"{late} {skipped} {took:.2f} {back} {list(c.recent)}"


@case("late at night, then nothing for hours: the first take in the morning waits for the cloud")
def _():
    c = make_cloud(timeout_s=3.0, grace_s=0.3, min_seconds=5)
    app = stand_in(c)
    state.update(reply=(200, {"text": "Облако"}), delay=0.8)
    late = [cloud_text(app, 6.0) for _ in range(3)]
    time.sleep(0.8)
    state["delay"] = 0.1
    c.recent_at -= 3600                                    # an hour without a take
    morning = cloud_text(app, 6.0)
    state["delay"] = 0.0
    return late == [HERE] * 3 and morning == "Облако.", f"{late} {morning!r} {list(c.recent)}"


@case("an answer that trickles in: given up at the limit for the whole request")
def _():
    state.update(reply=(200, {"text": "поздно-поздно-поздно"}), delay=0.0, trickle=0.15)
    c = make_cloud()
    t0 = time.perf_counter()
    try:
        c.transcribe(secs(1), 25.0)
        err = ""
    except Exception as exc:
        err = type(exc).__name__
    took = time.perf_counter() - t0
    state["trickle"] = 0
    return err == "TimeoutError" and took < 1.6, f"{err} {took:.2f} s"


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
