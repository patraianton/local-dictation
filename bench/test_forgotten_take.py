# -*- coding: utf-8 -*-
"""A take nobody stopped, with a few words from the room in it, is not pasted (10.10.2026).

The hands-free silence rules count any noise in the room as a voice, so a
door now and then keeps a forgotten take going until max_seconds. What gets
pasted then is whatever somebody said nearby. stt.asr.forgotten_take judges
such a take by how much speech it holds. Synthetic sound and a stand-in for
the speech filter; nothing real is read.

    ..\\.venv\\Scripts\\python.exe test_forgotten_take.py
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt import asr as A  # noqa: E402
from stt import config as cfg_mod  # noqa: E402

SR = 16000


def chunks_of(seconds):
    """A stand-in speech filter that finds `seconds` of speech in any sound."""
    return lambda audio: [(SR, SR + int(seconds * SR))] if seconds else []


def main() -> int:
    bad = 0

    def check(name, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    real_vad = A.speech_chunks
    sound = K.speech(4.0)
    try:
        # The judgement itself.
        for secs, want in ((0.0, True), (4.5, True), (14.9, True), (17.0, True), (29.9, True), (30.0, False), (47.0, False)):
            A.speech_chunks = chunks_of(secs)
            check(f"{secs:.1f} s of speech: forgotten", A.forgotten_take(sound), want)
        check("empty take: not this rule", A.forgotten_take(np.zeros(0, np.float32)), False)
    finally:
        A.speech_chunks = real_vad

    # The service: only a take that ran into max_seconds is asked about.
    tmp = Path(tempfile.mkdtemp())
    old = (cfg_mod.LOG_DIR, cfg_mod.REC_DIR)
    cfg_mod.LOG_DIR, cfg_mod.REC_DIR = tmp / "logs", tmp / "rec"
    pasted, logs = [], []
    real_log, real_paste = M.log, M.paste_text
    M.log = logs.append
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    try:
        d = K.make_processor()
        d.max_seconds = 4
        d.transcribe_resilient = lambda audio, secs=None: ("Pass the salt over there.", 0.01)

        A.speech_chunks = chunks_of(2.0)
        d.process(K.speech(4.0), preroll_s=0.0)       # ran into the limit, 2 s of speech
        d.process(K.speech(2.5), preroll_s=0.0)       # stopped by hand, same 2 s
        A.speech_chunks = chunks_of(60.0)
        d.process(K.speech(4.0), preroll_s=0.0)       # ran into the limit, a real dictation

        day = f"{datetime.now():%Y-%m-%d}"
        path = tmp / "logs" / "dropped" / f"{day}.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        check("the forgotten take is dropped as «forgotten»", [r["reason"] for r in rows], ["forgotten"])
        check("its words and sound are kept", bool(rows and rows[0]["raw"] and rows[0]["wav"]), True)
        check("the take stopped by hand and the real long one are pasted", len(pasted), 2)
        check("the log says why", any("maximum length" in x for x in logs), True)
        check("the screen says so for a while",
              any("forgotten" in t and h >= 5 for _s, t, h in d.hud.calls), True)
    finally:
        A.speech_chunks = real_vad
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR = old
        M.log, M.paste_text = real_log, real_paste
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
