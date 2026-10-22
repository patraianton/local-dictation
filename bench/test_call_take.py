# -*- coding: utf-8 -*-
"""A take that ran into max_seconds while a call had the microphone is not pasted (22.10.2026).

A hands-free take started by accident keeps going through a call: the person
is talking the whole time, so neither the quiet rules nor forgotten_take stop
it, and at max_seconds five minutes of one side of the call were pasted into
whatever window was in front (about ten times since August). When another
program used the microphone during such a take, the text goes to the board
only. A take stopped by hand, or a long one with no call, is pasted as before.
Invented sound and text; nothing real is read.

    ..\\.venv\\Scripts\\python.exe test_call_take.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt import asr as A  # noqa: E402
from stt import config as cfg_mod  # noqa: E402
from stt import miclisteners as L  # noqa: E402

SR = 16000


def main() -> int:
    bad = 0

    def check(name, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    tmp = Path(tempfile.mkdtemp())
    old = (cfg_mod.LOG_DIR, cfg_mod.REC_DIR)
    cfg_mod.LOG_DIR, cfg_mod.REC_DIR = tmp / "logs", tmp / "rec"
    pasted, logs = [], []
    real_log, real_paste = M.log, M.paste_text
    real_vad, real_within = A.speech_chunks, L.recorded_within
    M.log = logs.append
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    A.speech_chunks = lambda audio: [(SR, SR + 60 * SR)]     # plenty of speech
    try:
        d = K.make_processor()
        d.max_seconds = 4
        d.transcribe_resilient = lambda audio, secs=None: ("Yes, we can meet on Friday.", 0.01)

        L.recorded_within = lambda s: {"zoom", "zoom.exe"}
        d.process(K.speech(4.0), preroll_s=0.0)       # ran into the limit during a call
        check("the call take is not pasted", pasted, [])
        check("its text is on the board", d.learner.rows[-1]["final"], "Yes, we can meet on Friday.")
        check("the record says why", d.learner.rows[-1].get("not_pasted"), "call: zoom")
        check("the log says why", any("call" in x and "not pasted" in x for x in logs), True)
        check("the screen says so for a while",
              any("call" in t and h >= 5 for _s, t, h in d.hud.calls), True)

        d.process(K.speech(2.5), preroll_s=0.0)       # stopped by hand during the same call
        check("a take stopped by hand during a call is pasted", len(pasted), 1)

        L.recorded_within = lambda s: set()
        d.process(K.speech(4.0), preroll_s=0.0)       # ran into the limit, no call
        check("a long dictation with no call is pasted", len(pasted), 2)

        L.recorded_within = lambda s: {"python", "python.exe", "pythonw.exe"}
        d.process(K.speech(4.0), preroll_s=0.0)       # only our own recorder
        check("our own recording does not count as a call", len(pasted), 3)
    finally:
        A.speech_chunks, L.recorded_within = real_vad, real_within
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR = old
        M.log, M.paste_text = real_log, real_paste
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
