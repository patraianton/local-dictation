# -*- coding: utf-8 -*-
"""A take said in English is read as English, not translated (30.10.2026).

With the language held at "ru", an English sentence was now and then pasted as
its Russian translation. The take is asked its language first; above
[asr] english_above it is read with "en". Checked without a GPU and without
loading any recognizer: the model here is a fake that answers fixed shares.

    ..\\.venv\\Scripts\\python.exe test_english_take.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import Asr  # noqa: E402

SR = 16000


class FakeModel:
    def __init__(self, p_en=0.0, fail=None, text="Some made-up words here."):
        self.p_en, self.fail, self.text = p_en, fail, text
        self.langs, self.detects = [], 0

    def detect_language(self, audio, vad_filter=False):
        self.detects += 1
        if self.fail:
            raise self.fail
        return ("en" if self.p_en > 0.5 else "ru", max(self.p_en, 1 - self.p_en),
                [("en", self.p_en), ("ru", 1 - self.p_en)])

    def transcribe(self, audio, **kw):
        self.langs.append(kw["language"])
        return [SimpleNamespace(text=self.text)], None


def make(p_en=0.0, above=0.5, fail=None, language="ru", window=0.0):
    asr = Asr({"asr": {"model": "large-v3", "short_model": "", "english_above": above,
                       "english_window_s": window, "language": language,
                       "reread_early_end": False}}, ["Alpha Tool"])
    asr.model = FakeModel(p_en, fail)
    return asr


def read(asr, seconds=3.0):
    asr.transcribe(np.zeros(int(SR * seconds), dtype=np.float32))
    return asr.model.langs


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("an English take is read with en")
def _():
    asr = make(p_en=0.99)
    langs = read(asr)
    return langs == ["en"] and asr.take_language == "en" and asr.last_p_en == 0.99, f"{langs}"


@case("a Russian take with English terms stays ru")
def _():
    asr = make(p_en=0.18)
    langs = read(asr)
    return langs == ["ru"] and asr.last_p_en == 0.18, f"{langs}"


@case("off (english_above = 0): not even asked")
def _():
    asr = make(p_en=0.99, above=0.0)
    langs = read(asr)
    return langs == ["ru"] and asr.model.detects == 0 and asr.last_p_en is None, f"{langs}"


@case("an odd failure of the check leaves the take in ru")
def _():
    asr = make(p_en=0.99, fail=ValueError("empty after VAD"))
    langs = read(asr)
    return langs == ["ru"] and asr.last_p_en is None, f"{langs}"


@case("a dead card in the check is passed up, to be reloaded")
def _():
    asr = make(p_en=0.99, fail=RuntimeError("CUDA failed with error unspecified launch failure"))
    try:
        read(asr)
    except RuntimeError:
        return True, ""
    return False, "no error came up"


@case("the next Russian take goes back to ru")
def _():
    asr = make(p_en=0.99)
    read(asr)
    asr.model.p_en = 0.01
    langs = read(asr)
    return langs == ["en", "ru"], f"{langs}"


@case("an English take is not weighed for a question mark")
def _():
    asr = make(p_en=0.99)
    read(asr)
    return asr.question_score(np.zeros(SR, dtype=np.float32), "Is it ready.") is None, ""


@case("the language set to en: nothing is asked")
def _():
    asr = make(p_en=0.0, language="en")
    langs = read(asr)
    return langs == ["en"] and asr.model.detects == 0, f"{langs}"


@case("with a window: not asked before any English take")
def _():
    asr = make(p_en=0.99, window=300)
    asr.model.text = "Сделай отчёт по складу."
    langs = read(asr)
    return langs == ["ru"] and asr.model.detects == 0, f"{langs}"


@case("with a window: asked right after a take that came out in English")
def _():
    asr = make(p_en=0.99, window=300)
    asr.model.text = "Send the draft to the team."
    read(asr)                      # came out English on its own: opens the window
    langs = read(asr)
    return langs == ["ru", "en"] and asr.model.detects == 1, f"{langs}"


@case("with a window: not asked once the window has passed")
def _():
    asr = make(p_en=0.99, window=300)
    asr.model.text = "Send the draft to the team."
    read(asr)
    asr._english_at -= 301
    asr.model.text = "Сделай отчёт по складу."
    read(asr)
    return asr.model.detects == 0, f"{asr.model.detects}"


@case("with a window: a Russian take with a few English terms does not open it")
def _():
    asr = make(p_en=0.99, window=300)
    asr.model.text = "Открой Lavish и проверь PostHog."
    read(asr)
    read(asr)
    return asr.model.detects == 0, f"{asr.model.detects}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in CASES:
        ok, info = fn()
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
