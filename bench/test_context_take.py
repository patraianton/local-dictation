# -*- coding: utf-8 -*-
"""A take dictated into a Claude window in herdr gets that window's terms (24.10.2026).

herdr and the transcript are faked; the hint is read as the recognizer sees it.

    ..\\.venv\\Scripts\\python.exe test_context_take.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import __main__ as M  # noqa: E402
from stt import paste as paste_mod  # noqa: E402

USUAL = "Термины и названия: Анна, Claude."
RANKED = ["Анна", "Борис", "Вера", "Глеб", "Claude", "Sonnet"]


class FakeAsr:
    def __init__(self):
        self.prompt = USUAL
        self.seen = []

    def transcribe(self, audio, speech_s=None):
        self.seen.append(self.prompt)
        return "текст", 0.01

    def looks_like_lost_gpu(self, exc):
        return False


class FakeContext:
    def __init__(self, terms):
        self._terms = terms

    def terms(self, session):
        return self._terms if session == "s-1" else []


def rig(front="herdr.exe", session="s-1", terms=("ICP", "IMO"), on=True):
    d = object.__new__(M.Dictation)
    d.asr = FakeAsr()
    d.cfg = {"asr": {"prompt_terms": 6, "prompt_style": "list"}}
    d.hint_terms = RANKED
    d.context_hint = on
    d.context = FakeContext(list(terms))
    paste_mod.foreground_exe = lambda: front
    paste_mod.herdr_focused_session = lambda: session
    return d


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("a Claude window in herdr: its terms after the pinned names, the usual hint back after")
def _():
    d = rig()
    d.transcribe_take(None)
    want = "Термины и названия: Анна, Борис, Вера, Глеб, ICP, IMO."
    return d.asr.seen == [want] and d.asr.prompt == USUAL and d.last_context == ["ICP", "IMO"], \
        f"{d.asr.seen} / {d.asr.prompt}"


@case("another program in front: the usual hint")
def _():
    d = rig(front="chrome.exe")
    d.transcribe_take(None)
    return d.asr.seen == [USUAL] and d.last_context is None, f"{d.asr.seen}"


@case("a herdr pane with no Claude session: the usual hint")
def _():
    d = rig(session="")
    d.transcribe_take(None)
    return d.asr.seen == [USUAL], f"{d.asr.seen}"


@case("switched off in the settings: the usual hint")
def _():
    d = rig(on=False)
    d.transcribe_take(None)
    return d.asr.seen == [USUAL], f"{d.asr.seen}"


@case("herdr failing: the usual hint, and the take still goes through")
def _():
    d = rig()

    def boom():
        raise OSError("herdr is gone")
    paste_mod.herdr_focused_session = boom
    M.log = lambda msg: None
    text, _ = d.transcribe_take(None)
    return text == "текст" and d.asr.seen == [USUAL], f"{d.asr.seen}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, fn in CASES:
        ok, info = fn()
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
