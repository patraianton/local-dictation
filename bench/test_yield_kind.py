# -*- coding: utf-8 -*-
"""A press with no pre-roll names which hand-over took the microphone (18.10.2026).

In a week of takes 115 presses in a chat program found the microphone handed
over and lost their first half second. The log said only "handed to a program
in yield_to", which cannot tell the chance given when the window comes to the
front from the one that comes round every so often, and those two would be
fixed differently. The real methods of Dictation are run on a stand-in with the
clock, the window in front and the record of who records all made up:

    ..\\.venv\\Scripts\\python.exe test_yield_kind.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stt.__main__ as M  # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"[{'v' if ok else 'X'}] {name}: {got!r}")
    if not ok:
        FAIL.append(name)


def main() -> int:
    state = {"front": "chat.exe", "recording": set(), "known": set(), "clock": 1000.0}
    M.paste_mod.foreground_exe = lambda: state["front"]
    M.time.perf_counter = lambda: state["clock"]

    def is_recording(exe, within_s=0.0):
        return exe in (state["known"] if within_s > 3600 else state["recording"])

    M.listeners_mod.is_recording = is_recording
    lines = []
    M.log = lines.append

    app = SimpleNamespace(
        yield_to={"chat.exe", "call.exe"}, yield_probe_s=2.5, yield_probe_every_s=25.0,
        yield_probe_hot_every_s=4.0, yield_known_s=86400.0, yield_grace_s=120.0,
        _front_exe="", _front_since=0.0, _probe_until=0.0, yield_kind="",
        recorder=SimpleNamespace(closed_at=0.0, closed_why=""))
    hold = lambda: M.Dictation._may_hold_mic(app)  # noqa: E731

    def cold_press():
        lines.clear()
        M.Dictation._log_cold_press(app)
        return lines[-1]

    state["front"] = "editor.exe"
    check("an editor in front: held", hold(), True)

    state["front"], state["clock"] = "chat.exe", 1001.0
    check("the chat comes to the front: let go", hold(), False)
    app.recorder.closed_at, app.recorder.closed_why = 1001.0, "handed to a program in yield_to"
    state["clock"] = 1002.0
    check("a press 1 s later names the window coming to the front", cold_press(),
          "no pre-roll on this press: the microphone was let go 1.0 s before "
          "(handed to a program in yield_to: chat.exe came to the front); in front: chat.exe")

    state["clock"] = 1003.6
    check("the chance is over: held again", hold(), True)
    state["clock"] = 1028.6
    check("25 s on, its chance comes round: let go", hold(), False)
    check("…and the press names that chance", "chat.exe's chance every 25 s" in cold_press(), True)

    state["front"], state["recording"], state["clock"] = "call.exe", {"call.exe"}, 1040.0
    check("a program that records: let go", hold(), False)
    check("…and the press names the recording", "call.exe records" in cold_press(), True)

    app.recorder.closed_why = "not held any longer (hot_ms ran out)"
    check("the other reasons are left as they were", cold_press(),
          "no pre-roll on this press: the microphone was let go 39.0 s before "
          "(not held any longer (hot_ms ran out)); in front: call.exe")

    print(f"\n{'all passed' if not FAIL else f'{len(FAIL)} failed'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
