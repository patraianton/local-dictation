# -*- coding: utf-8 -*-
"""A press that finds no sound before it says what had the microphone.

Recorder remembers why and when it last let the microphone go; Dictation.start
logs one line when a take begins with no pre-roll, and none when the pre-roll
is there. Fake microphone, no sound card.

    ..\\.venv\\Scripts\\python.exe test_cold_press.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt import audio as A  # noqa: E402

bad = 0


def check(ok, what, detail=""):
    global bad
    bad += not ok
    print(f"[{'v' if ok else 'X'}] {what}" + ("" if ok else f"  ({detail})"))


def main() -> int:
    lines = []
    M.log = lines.append
    M.paste_mod.foreground_exe = lambda: "Chat.exe"

    # --- 1. the recorder keeps the reason; a close with nothing open keeps the old one
    rec = A.Recorder([None], K.SR, preroll_s=0.2, hot_s=60.0)
    check(rec.closed_why == "not opened yet", "a new recorder says it was never opened")
    rec._stream = K.FakeStream()
    rec._close("handed to a program in yield_to")
    check(rec.closed_why == "handed to a program in yield_to", "the reason is kept", rec.closed_why)
    t = rec.closed_at
    rec._close("given back on request")
    check(rec.closed_why == "handed to a program in yield_to" and rec.closed_at == t,
          "closing a closed microphone changes nothing", rec.closed_why)

    # --- 2. a press with a full pre-roll logs nothing
    r = K.Rig()
    time.sleep(0.4)                   # the ring fills
    r.d.on_down()
    check(r.rec.pending_preroll_s > 0.1, "the open microphone gives a pre-roll",
          f"{r.rec.pending_preroll_s:.2f}")
    check(not any("no pre-roll" in x for x in lines), "... and no line about it", lines)
    r.d.on_up()
    K.wait_for(lambda: len(r.taken) == 1)
    r.done()

    # --- 3. a press after the microphone was handed over logs what had it
    lines.clear()
    r = K.Rig()
    r.rec._close("handed to a program in yield_to")
    # The press opens it again, from cold: an empty ring.
    r.rec._open = lambda: setattr(r.rec, "_stream", K.FakeStream())
    r.d.on_down()
    check(r.rec.pending_preroll_s < 0.05, "a stream opened on the press has no pre-roll",
          f"{r.rec.pending_preroll_s:.2f}")
    hit = [x for x in lines if "no pre-roll" in x]
    check(len(hit) == 1, "one line about it", lines)
    check(bool(hit) and "handed to a program in yield_to" in hit[0] and "chat.exe" in hit[0],
          "the line names the reason and the window in front", hit)
    r.d.on_up()
    K.wait_for(lambda: len(r.taken) == 1)
    r.done()

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
