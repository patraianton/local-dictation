# -*- coding: utf-8 -*-
r"""The key, the take and the tail: what happens between a press and the paste.

No microphone, no model, no keyboard hooks: the sound card is a thread that
hands the recorder blocks of 4096 samples at 48 kHz (85 ms, as the real driver
does, measured 30.09.2026), and the key handlers of Dictation are called
directly.

What this guards (all found in the audit of 30.09.2026, fixed 01.10.2026):
  1. a press during the tail of the previous take was swallowed and the whole
     phrase vanished -> now it resumes the take;
  2. a short tap turned hands-free on and the next press stopped it at once,
     splitting the phrase -> now the stop is at the RELEASE of that press;
  3. key auto-repeat started new takes -> the key's own state is tracked;
  4. a take that found the previous one still processing was skipped -> waits;
  5. the tail counted empty polls as silence -> counts the samples received;
  6. dropped takes left no trace -> logs/dropped + recordings/<day>/dropped;
  7. no question mark glued after a phrase that was broken off;
  8. dropped sound blocks are counted per take.

    ..\.venv\Scripts\python.exe test_key_flow.py
"""
import inspect
import json
import shutil
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Imported before anything is printed: stt.__main__ swaps the output stream.
from stt import __main__ as M  # noqa: E402
from stt import audio as A  # noqa: E402
from stt import config as cfg_mod  # noqa: E402

SR = 48000
BLOCK = 4096                      # 85 ms, what the driver really delivers
BLOCK_S = BLOCK / SR
LOUD, QUIET = 0.2, 0.0005
RNG = np.random.default_rng(7)

bad = 0
M.paste_mod.foreground_exe = lambda: "test.exe"


def check(ok, why, detail=""):
    global bad
    bad += 0 if ok else 1
    print(f"[{'v' if ok else 'X'}] {why}")
    if not ok and detail:
        print(f"      {detail}")


def wait_for(cond, timeout=4.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------- fakes
class FakeStream:
    def stop(self):
        pass

    def close(self):
        pass


class Mic:
    """The sound card: hands the recorder one block every 85 ms, in real time.

    `plan` is a list of levels for the first blocks; after it, `level`.
    """

    def __init__(self, rec, plan=None):
        self.rec = rec
        self.plan = list(plan or [])
        self.level = QUIET
        self.sent = 0
        self.status = {}              # block number -> driver status flag
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._t.start()
        return self

    def _loop(self):
        t0 = time.perf_counter()
        while True:
            due = t0 + (self.sent + 1) * BLOCK_S
            if self._stop.wait(max(0.0, due - time.perf_counter())):
                return
            lvl = self.plan[self.sent] if self.sent < len(self.plan) else self.level
            block = (RNG.standard_normal(BLOCK) * lvl).astype(np.float32)
            n = self.sent
            self.sent += 1
            self.rec._callback(block, BLOCK, None, self.status.get(n))

    def halt(self):
        self._stop.set()
        self._t.join(timeout=1.0)


class Hud:
    def __init__(self):
        self.seen = []
        self.calls = []               # (state, text, hide_after), for what stays on the screen

    def set(self, state, text="", hide_after=0.0):
        self.seen.append((state, text))
        self.calls.append((state, text, hide_after))


class Ducker:
    def __init__(self):
        self.ducked = 0
        self.restored = 0

    def duck(self):
        self.ducked += 1

    def restore(self):
        self.restored += 1


def make_recorder(preroll_s=0.2, plan=None):
    rec = A.Recorder([None], SR, preroll_s=preroll_s, hot_s=60.0)
    rec._stream = FakeStream()        # "the stream is open": no sounddevice involved
    rec._open_sr, rec._open_ch = SR, 1
    rec.recipe = (None, SR, 1)
    rec._last_block_at = time.perf_counter()
    mic = Mic(rec, plan).start()
    return rec, mic


class Rig:
    """A Dictation with the key handlers alive and everything else faked."""

    def __init__(self, tap_ms=300, tail_s=0.5):
        d = object.__new__(M.Dictation)
        d.tap_ms, d.max_seconds = tap_ms, 300
        d.tail_s, d.tail_quiet_ms = tail_s, 120
        d.hud, d.ducker = Hud(), Ducker()
        d.mic_cooldown, d.hot_window = 0.0, ""
        M.Dictation._init_flow(d)
        self.rec, self.mic = make_recorder()
        d.recorder = self.rec
        self.starts = 0
        real_start = self.rec.start

        def counting_start():
            self.starts += 1
            return real_start()

        self.rec.start = counting_start
        self.taken = []               # what process() was handed
        # process() answers whether the text was pasted; here it always was.
        d.process = lambda data, preroll_s=None, xruns=0, xrun_note="": \
            (self.taken.append((data, preroll_s, xruns)), True)[1]
        self.d = d

    def speak(self, level):
        self.mic.level = level

    def done(self):
        self.mic.halt()
        self.rec.close()


def rms_windows(audio, win_s=0.1):
    n = int(16000 * win_s)
    return [float(np.sqrt(np.mean(audio[i:i + n] ** 2)))
            for i in range(0, len(audio) - n, n)]


# ---------------------------------------------------------------- 1. a plain hold
def test_hold():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    check(d.recording and not d.locked, "press starts a take")
    time.sleep(0.5)
    d.on_up()
    r.speak(QUIET)
    check(not d.recording, "release ends the take for the key")
    check(wait_for(lambda: len(r.taken) == 1), "hold -> release: the take is handed to process")
    time.sleep(0.4)
    check(len(r.taken) == 1, "... and only once", f"{len(r.taken)} takes")
    secs = len(r.taken[0][0]) / 16000
    check(0.5 <= secs <= 1.6, "the take holds what was said (+ a short tail)", f"{secs:.2f} s")
    check(r.d.ducker.ducked == 1 and r.d.ducker.restored == 1,
          "other audio is ducked once and given back once")
    r.done()


# ---------------------------------------------------------------- 2. tap, then hold: one take
def test_tap_then_hold():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.05)
    d.on_up()
    check(d.recording and d.locked, "a short tap turns hands-free on")
    time.sleep(0.4)                   # first part of the phrase, spoken while it records
    d.on_down()                       # he did not notice hands-free: presses to speak
    r.speak(0.6)
    time.sleep(0.15)
    check(d.recording and len(r.taken) == 0,
          "in hands-free the press does NOT stop anything")
    time.sleep(0.4)
    d.on_up()                         # long hold, released at the end of the phrase
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1), "release stops it: one take handed to process")
    time.sleep(0.4)
    check(len(r.taken) == 1 and r.starts == 1,
          "tap + hold = ONE take, no second one started",
          f"takes {len(r.taken)}, recorder starts {r.starts}")
    w = rms_windows(r.taken[0][0])
    # White noise loses two thirds of its power in the 48 -> 16 kHz resampling: 0.2 reads as 0.115,
    # 0.6 as 0.35.
    check(sum(1 for x in w if 0.07 < x < 0.17) >= 3 and sum(1 for x in w if x > 0.28) >= 3,
          "and it holds both the part before the press and the part after it",
          f"windows {[round(x, 2) for x in w]}")
    r.done()


# ---------------------------------------------------------------- 3. a press during the tail
def test_resume_in_tail():
    r = Rig(tail_s=0.7)
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.5)
    d.on_up()                         # the tail starts; he is still speaking, so it runs on
    time.sleep(0.15)
    d.on_down()                       # ...and he presses again inside it
    check(d.recording and not d.locked, "a press during the tail continues the take")
    check(r.d.hud.seen[-1][0] == "rec", "the screen says recording again")
    time.sleep(0.1)
    d.on_up()                         # a short press: must end the take, not turn hands-free on
    r.speak(QUIET)
    check(not d.locked and not d.recording,
          "a resumed take never turns hands-free on a short press")
    check(wait_for(lambda: len(r.taken) == 1), "the resumed take is processed after its next release")
    time.sleep(0.5)
    check(len(r.taken) == 1 and r.starts == 1,
          "press in the tail = one take, no second one", f"takes {len(r.taken)}, starts {r.starts}")
    secs = len(r.taken[0][0]) / 16000
    check(secs >= 0.5 + 0.15 + 0.1 - 0.05, "its sound is continuous across the press", f"{secs:.2f} s")
    check(r.d.ducker.ducked == 1 and r.d.ducker.restored == 1,
          "the resumed take neither un-ducks nor ducks twice",
          f"ducked {r.d.ducker.ducked}, restored {r.d.ducker.restored}")
    r.done()

    # The press comes before the thread that stops has even begun.
    r = Rig(tail_s=0.7)
    d = r.d
    real_finish = d._finish
    d._finish = lambda req=None, notice="": (time.sleep(0.2), real_finish(req, notice))
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.4)
    d.on_up()
    time.sleep(0.05)                  # the finishing thread is still asleep
    d.on_down()
    check(d.recording, "a press before the stopping thread has started still resumes")
    time.sleep(0.4)
    check(len(r.taken) == 0, "...and the late stop hands over nothing")
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1) and r.starts == 1, "one take in the end")
    r.done()

    # A take that was SHORT (stopped by the toggle button 0.2 s in) and is resumed by a short
    # press of the key: held for less than tap_ms in all, yet the release must end it.
    r = Rig(tail_s=0.7)
    d = r.d
    r.speak(LOUD)
    d.on_toggle()
    time.sleep(0.1)
    d.on_toggle()
    time.sleep(0.1)
    d.on_down()
    check(d.recording and not d.locked, "a press during the tail of a toggled take resumes it")
    time.sleep(0.05)
    d.on_up()
    r.speak(QUIET)
    check(not d.locked and not d.recording,
          "a resumed take that is shorter than tap_ms still ends at the release (no hands-free)")
    check(wait_for(lambda: len(r.taken) == 1) and r.starts == 1, "and is processed once")
    r.done()

    # A press AFTER the tail is over is a new take, as always.
    r = Rig(tail_s=0.3)
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.4)
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1), "first take done")
    d.on_down()
    time.sleep(0.4)
    d.on_up()
    check(wait_for(lambda: len(r.taken) == 2) and r.starts == 2,
          "a press after the take was handed over starts a new one")
    r.done()


# ---------------------------------------------------------------- 4. auto-repeat
def test_auto_repeat():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    for _ in range(12):               # the keyboard repeats while he holds
        time.sleep(0.04)
        d.on_down()
    check(r.starts == 1, "repeats of a held key do not start anything", f"starts {r.starts}")
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1), "one take")

    # Hands-free, then a held press with repeats: no stop at the press, no new take.
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.05)
    d.on_up()
    time.sleep(0.3)
    d.on_down()
    for _ in range(10):
        time.sleep(0.04)
        d.on_down()
    check(d.recording and r.starts == 1 and not r.taken,
          "repeats of a press in hands-free change nothing",
          f"recording {d.recording}, starts {r.starts}, takes {len(r.taken)}")
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1), "the release stops it")
    time.sleep(0.5)
    check(r.starts == 1 and len(r.taken) == 1,
          "nothing new starts after the stop", f"starts {r.starts}, takes {len(r.taken)}")
    r.done()

    # A lost release must not wedge the key for good.
    r = Rig(tail_s=0.2)
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.5)
    d._down_at -= M.KEY_REPEAT_GAP_S + 1.0     # no repeat for ages, and the release never came
    d.on_down()                                 # a press: must be taken as new, not as a repeat
    check(d.key_down, "after a missed release the next press is seen as a press")
    time.sleep(0.1)
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) >= 1) and not d.recording,
          "and the key keeps working", f"takes {len(r.taken)}")
    r.done()


# ---------------------------------------------------------------- 5. tap to stop
def test_tap_to_stop():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.05)
    d.on_up()
    check(d.locked, "tap: hands-free")
    time.sleep(0.5)
    d.on_down()
    check(d.recording, "the stopping press does nothing yet")
    time.sleep(0.08)
    d.on_up()
    r.speak(QUIET)
    check(not d.recording, "its release stops the take")
    check(wait_for(lambda: len(r.taken) == 1), "and the take goes to process")
    r.done()


# ---------------------------------------------------------------- 6. Esc
def test_esc():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_down()
    time.sleep(0.3)
    d.on_esc()
    check(not d.recording and not r.rec.recording, "Esc cancels the take")
    check(r.d.ducker.restored == 1, "and gives the other audio back")
    for _ in range(4):                # the key is still held and repeating
        time.sleep(0.04)
        d.on_down()
    check(r.starts == 1 and not d.recording,
          "the held key's repeats after Esc do not start a new take", f"starts {r.starts}")
    d.on_up()
    check(not d.recording, "its release does nothing")
    time.sleep(0.2)
    check(len(r.taken) == 0, "nothing was processed")
    d.on_down()
    time.sleep(0.4)
    d.on_up()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1) and r.starts == 2,
          "a fresh press after the release starts a take again")
    r.done()


# ---------------------------------------------------------------- 7. the toggle button
def test_toggle():
    r = Rig()
    d = r.d
    r.speak(LOUD)
    d.on_toggle()
    check(d.recording and d.locked, "toggle: starts hands-free")
    time.sleep(0.4)
    d.on_toggle()
    r.speak(QUIET)
    check(not d.recording, "toggle again: stops")
    check(wait_for(lambda: len(r.taken) == 1), "one take")
    r.done()

    r = Rig(tail_s=0.7)
    d = r.d
    r.speak(LOUD)
    d.on_toggle()
    time.sleep(0.4)
    d.on_toggle()                     # stop; the tail runs because he is still speaking
    time.sleep(0.15)
    d.on_toggle()                     # pressed during the tail
    check(d.recording and d.locked, "toggle during the tail resumes the take and locks it")
    time.sleep(0.3)
    check(len(r.taken) == 0, "nothing was handed over meanwhile")
    d.on_toggle()
    r.speak(QUIET)
    check(wait_for(lambda: len(r.taken) == 1) and r.starts == 1, "one take in the end")
    r.done()

    # The microphone refuses: no phantom hands-free.
    r = Rig()
    d = r.d

    def refuse():
        raise RuntimeError("busy")

    r.rec.start = refuse
    d.on_toggle()
    check(not d.recording and not d.locked,
          "a microphone that will not open leaves nothing half-started")
    r.done()


# ---------------------------------------------------------------- 8. busy waits, in order
def test_busy_waits():
    logs = []
    real_log = M.log
    M.log = logs.append
    try:
        d = object.__new__(M.Dictation)
        d.hud = Hud()
        M.Dictation._init_flow(d)
        d.recorder = type("R", (), {"last_preroll_s": 0.0})()
        d.cfg = {}
        order = []

        def slow(data, preroll_s, xruns, xrun_note):
            order.append(int(data[0]))
            time.sleep(1.2 if data[0] == 1 else 0.05)

        d._process_take = slow
        threads = []
        for i in (1, 2, 3):
            t = threading.Thread(target=d.process, args=(np.array([float(i)]),))
            t.start()
            threads.append(t)
            time.sleep(0.08)
        for t in threads:
            t.join(timeout=10)
        check(order == [1, 2, 3], "takes that arrive while one is processed wait and go in order",
              f"order {order}")
        check(not any("skipping" in x for x in logs), "none is skipped")
        check(any("waited" in x for x in logs), "waiting longer than a second is logged",
              str(logs))

        # A take that waits too long gives up, keeps its sound, and does not jam the line.
        tmp = Path(tempfile.mkdtemp())
        old = (cfg_mod.LOG_DIR, cfg_mod.REC_DIR, M.BUSY_WAIT_S)
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR, M.BUSY_WAIT_S = tmp / "logs", tmp / "rec", 0.8
        try:
            d = object.__new__(M.Dictation)
            d.hud = Hud()
            M.Dictation._init_flow(d)
            d.recorder = type("R", (), {"last_preroll_s": 0.0})()
            d.cfg = {}
            order = []

            def slow2(data, preroll_s, xruns, xrun_note):
                order.append(int(data[0]))
                time.sleep(2.4 if data[0] == 1 else 0.02)

            d._process_take = slow2
            # first: a long one
            first = threading.Thread(target=d.process, args=(np.array([1.0]),))
            first.start()
            time.sleep(0.05)
            second = threading.Thread(target=d.process,
                                      args=(np.full(8000, 0.2, dtype=np.float32),))
            second.start()                          # gives up after 0.8 s
            second.join(timeout=5)
            time.sleep(0.9)                         # t = ~1.75 s: the first still has 0.65 s to go
            third = threading.Thread(target=d.process, args=(np.array([3.0]),))
            third.start()
            first.join(timeout=10)
            third.join(timeout=10)
            check(order == [1, 3], "a take that gave up is struck off and does not jam the line",
                  f"order {order}")
            day = f"{datetime.now():%Y-%m-%d}"
            rows = [json.loads(x) for x in
                    (tmp / "logs" / "dropped" / f"{day}.jsonl").read_text(encoding="utf-8").splitlines()]
            check([x["reason"] for x in rows] == ["queue_timeout"] and rows[0]["wav"]
                  and Path(rows[0]["wav"]).exists(),
                  "its sound is kept as a dropped take", str(rows))
        finally:
            cfg_mod.LOG_DIR, cfg_mod.REC_DIR, M.BUSY_WAIT_S = old
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        M.log = real_log


# ---------------------------------------------------------------- 9. the tail
def blocks_in(audio):
    return len(audio) * 3 / BLOCK      # 16 kHz samples -> 48 kHz blocks


def run_tail(plan, release_at_block, tail_s=0.7, quiet_ms=120, level_after=QUIET):
    """Starts a take at block 0, releases when block `release_at_block` has arrived."""
    rec, mic = make_recorder(preroll_s=0.0, plan=plan)
    mic.level = level_after
    rec.start()
    while mic.sent < release_at_block:
        time.sleep(0.002)
    t0 = time.perf_counter()
    audio = rec.stop(tail_s=tail_s, quiet_ms=quiet_ms)
    waited = time.perf_counter() - t0
    delivered = mic.sent
    mic.halt()
    rec.close()
    return audio, waited, delivered


def test_tail():
    # Speech for 12 blocks, then silence. Released at block 10, mid-word.
    audio, waited, _ = run_tail([LOUD] * 12, 10)
    end_block = blocks_in(audio)
    check(end_block >= 14 - 0.6,
          "the tail does not stop on the first quiet block: it takes quiet_ms of received silence",
          f"the take ends at block {end_block:.1f}, the speech ended at 12, wanted 2 quiet blocks (14)")
    check(end_block <= 16.5, "...and it does stop once the silence has lasted",
          f"ends at block {end_block:.1f}")
    check(waited < 0.7 - 0.05, "so the early stop on silence is kept", f"waited {waited:.2f} s")

    # One quiet block in the middle of speech (a gap between two words) is not an end.
    audio, waited, _ = run_tail([LOUD] * 7 + [QUIET] + [LOUD] * 30, 6)
    end_block = blocks_in(audio)
    check(end_block >= 11,
          "a single quiet block between words does not end the take",
          f"ends at block {end_block:.1f}")

    # Sound that keeps coming is cut at the maximum, not before and not much later.
    audio, waited, _ = run_tail([LOUD] * 80, 6, tail_s=0.7)
    check(0.6 <= waited <= 0.95, "keeps going on sound up to the maximum tail (700 ms)",
          f"waited {waited:.2f} s")
    audio, waited, _ = run_tail([LOUD] * 80, 6, tail_s=0.4)
    check(0.32 <= waited <= 0.65, "the maximum is the one that was asked for", f"waited {waited:.2f} s")

    # A stream that delivers nothing does not hold the take for the whole tail.
    rec, mic = make_recorder(preroll_s=0.0, plan=[LOUD] * 6)
    rec.start()
    while mic.sent < 6:
        time.sleep(0.002)
    mic.halt()
    t0 = time.perf_counter()
    audio = rec.stop(tail_s=0.7)
    waited = time.perf_counter() - t0
    check(waited < 0.62 and len(audio) > 0, "a dead stream ends the tail early",
          f"waited {waited:.2f} s")
    rec.close()

    src = inspect.getsource(M.Dictation.__init__)
    check('mic_cfg.get("tail_ms", 700)' in src, "the default maximum tail is 700 ms")


# ---------------------------------------------------------------- 10. resume, at the recorder
def test_resume_recorder():
    rec, mic = make_recorder(plan=[LOUD] * 400)
    check(rec.resume() is False, "nothing to resume when no stop is pending")
    rec.start()
    time.sleep(0.4)
    req = rec.begin_stop()
    check(req is not None and rec.resume() is True, "a pending stop can be taken back")
    held = rec._samples()
    out = rec.stop(tail_s=0.5, request=req)
    check(out is None and rec.recording, "...and then stop() hands over nothing and the take stays armed")
    check(rec._samples() >= held, "with every chunk still in place")
    out = rec.stop(tail_s=0.0)
    check(out is not None and len(out) > 16000 * 0.3, "a later stop hands the whole take over",
          f"{0 if out is None else len(out)}")
    check(rec.resume() is False, "once the audio is handed over there is nothing to resume")
    # the race: resume against the commit of stop, many times
    both = neither = 0
    for _ in range(300):
        rec.start()
        barrier = threading.Barrier(2)
        res = {}
        req = rec.begin_stop()

        def do_stop():
            barrier.wait()
            res["audio"] = rec.stop(tail_s=0.0, request=req)

        def do_resume():
            barrier.wait()
            time.sleep(0.0002 * (_ % 7))
            res["resumed"] = rec.resume()

        t1, t2 = threading.Thread(target=do_stop), threading.Thread(target=do_resume)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        stopped = res["audio"] is not None
        if stopped == res["resumed"]:
            both += 1                  # both won, or neither did
        if not stopped:
            rec.stop(tail_s=0.0)
        elif rec.recording:
            neither += 1
    check(both == 0 and neither == 0,
          "against a racing resume exactly one of the two wins, 300 times in a row",
          f"inconsistent {both}, still armed after a stop {neither}")
    mic.halt()
    rec.close()


# ---------------------------------------------------------------- 11. dropped takes
class FakeAsr:
    last_model = "fake"


class FakeLearner:
    def __init__(self):
        self.rows = []

    def observe(self, pre, polished):
        return []

    def save_audio(self, audio, text, stamp):
        return f"fake/{stamp}.wav"

    def log(self, row):
        self.rows.append(row)


class FakePolisher:
    available = True
    reason = ""

    def polish(self, text):
        return text, 0.0, "ok"


class FakeFixes:
    def apply(self, text):
        return text, 0


class FakeGain:
    def adapt(self, peak, secs):
        return ""


def make_processor(keep_audio=True):
    d = object.__new__(M.Dictation)
    M.Dictation._init_flow(d)
    d.cfg = {"learn": {"keep_audio": keep_audio}, "paste": {}}
    d.hud = Hud()
    d.asr = FakeAsr()
    d.learner = FakeLearner()
    d.polisher = FakePolisher()
    d.fixes = FakeFixes()
    d.gain = FakeGain()
    d.max_seconds = 300
    d.fix_endings = False
    d.sound_questions = False
    d.last_sound_q = None
    d.last = {}
    d.recorder = type("R", (), {"last_preroll_s": 0.0})()
    d.transcribe_resilient = lambda audio, secs=None: ("", 0.01)
    return d


def speech(seconds, level=0.2):
    return (RNG.standard_normal(int(16000 * seconds)) * level).astype(np.float32)


def test_dropped():
    tmp = Path(tempfile.mkdtemp())
    old = (cfg_mod.LOG_DIR, cfg_mod.REC_DIR)
    cfg_mod.LOG_DIR, cfg_mod.REC_DIR = tmp / "logs", tmp / "rec"
    logs = []
    real_log, real_paste = M.log, M.paste_text
    pasted = []
    M.log = logs.append
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    try:
        d = make_processor()
        day = f"{datetime.now():%Y-%m-%d}"

        d.process(np.zeros(0, dtype=np.float32), preroll_s=0.0)                       # nothing at all
        d.process(speech(0.1), preroll_s=0.0)                                          # a tap
        d.process(speech(1.0, 0.0003), preroll_s=0.0)                                  # silence
        d.process(np.concatenate([speech(0.5, 0.05), speech(0.1)]), preroll_s=0.5)     # only the pre-roll is long
        d.transcribe_resilient = lambda audio, secs=None: ("", 0.01)
        d.process(speech(1.0), preroll_s=0.0)                                          # nothing recognized
        d.transcribe_resilient = lambda audio, secs=None: ("Продолжение следует...", 0.02)
        d.process(speech(1.0), preroll_s=0.0)                                          # the stock line

        def boom(audio, secs=None):
            raise RuntimeError("the card fell over")

        d.transcribe_resilient = boom
        d.process(speech(1.0), preroll_s=0.0)                                          # a crash
        d.transcribe_resilient = lambda audio, secs=None: ("Plan the first part.", 0.03)
        d.process(speech(1.0), preroll_s=0.25, xruns=2, xrun_note="input overflow")    # a normal take

        path = tmp / "logs" / "dropped" / f"{day}.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        reasons = [x["reason"] for x in rows]
        check(reasons == ["no_audio", "too_short", "silence", "too_short", "nothing_recognized",
                          "subtitle_ghost", "failed"],
              "every kind of dropped take leaves a row in logs/dropped/<day>.jsonl", str(reasons))
        need = {"id", "time", "reason", "seconds", "peak", "rms", "preroll_s", "wav"}
        check(all(need <= set(x) for x in rows), "each row has id, time, reason, seconds, peak, rms, "
              "preroll_s, wav", str([need - set(x) for x in rows]))
        check(rows[0]["wav"] is None and all(x["wav"] and Path(x["wav"]).exists() for x in rows[1:]),
              "the sound is kept for all of them (nothing to keep for an empty take)")
        with wave.open(rows[2]["wav"], "rb") as wf:
            check(wf.getframerate() == 16000 and wf.getnchannels() == 1 and wf.getnframes() == 16000,
                  "the wav is 16 kHz mono and as long as the take")
        check(rows[3]["seconds"] < 0.25 and rows[3]["preroll_s"] == 0.5,
              "seconds does not count the pre-roll", f"{rows[3]['seconds']} {rows[3]['preroll_s']}")
        check(rows[5]["raw"].startswith("Продолжение") and rows[4]["ms_asr"] == 10,
              "what the recognizer said (and how long it took) is in the row")
        check("falls over" in rows[6]["error"] or "fell over" in rows[6]["error"],
              "a crash is recorded with its error text", rows[6].get("error", ""))
        check(not list((tmp / "logs").glob("*.jsonl")),
              "nothing is written next to the real journal (store.py reads logs/*.jsonl)")
        check(pasted == ["Plan the first part."], "the one real take was pasted, no dropped one was")
        check(len(d.learner.rows) == 1 and d.learner.rows[0]["xruns"] == 2
              and d.learner.rows[0]["preroll_s"] == 0.25,
              "the journal row carries xruns and the take's own pre-roll",
              str({k: d.learner.rows[0].get(k) for k in ("xruns", "preroll_s")}))
        check(any("dropped block" in x and "input overflow" in x for x in logs),
              "dropped sound blocks are logged", str(logs[-6:]))

        # No audio kept when the owner turned it off, but the row stays.
        d2 = make_processor(keep_audio=False)
        d2.process(speech(0.1), preroll_s=0.0)
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        check(rows[-1]["reason"] == "too_short" and rows[-1]["wav"] is None,
              "with keep_audio off the row is written and no wav")

        # Never raises, even if the folder cannot be made.
        blocker = tmp / "blocker"
        blocker.write_text("a file where a folder should be", encoding="utf-8")
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR = blocker / "logs", blocker / "rec"
        try:
            d.process(speech(0.1), preroll_s=0.0)
            ok = True
        except Exception as exc:  # pragma: no cover
            ok = False
            print(exc)
        check(ok and any("could not keep a trace" in x for x in logs),
              "a folder that cannot be written does not raise; the log says so")
    finally:
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR = old
        M.log, M.paste_text = real_log, real_paste
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 12. xruns per take
def test_xruns():
    rec, mic = make_recorder(preroll_s=0.2)
    mic.status = {}
    rec._callback(np.zeros(BLOCK, dtype=np.float32), BLOCK, None, "input overflow")   # long ago
    time.sleep(0.5)
    rec.start()
    n0 = mic.sent
    mic.status[n0 + 2] = "input overflow"
    mic.status[n0 + 4] = "input underflow"
    while mic.sent < n0 + 8:
        time.sleep(0.005)
    out = rec.stop(tail_s=0.0)
    check(out is not None and rec.last_xruns == 2 and rec.xruns >= 3,
          "xruns of THIS take are counted apart from the running total",
          f"take {rec.last_xruns}, total {rec.xruns}")
    check(rec.last_xrun_kinds == ["input overflow", "input underflow"], "with what the driver called them",
          str(rec.last_xrun_kinds))
    rec.start()
    time.sleep(0.3)
    rec.stop(tail_s=0.0)
    check(rec.last_xruns == 0, "a clean take after it shows none")
    mic.halt()
    rec.close()


# ---------------------------------------------------------------- 13. no "?" after a broken-off phrase
class FakeAsrQ:
    def __init__(self, d):
        self.d = d
        self.asked = []

    def question_score(self, audio, text):
        self.asked.append(text)
        return self.d


def test_cut_off_question():
    class D:
        pass

    ask = M.Dictation.ask_the_sound
    data = np.zeros(16000, dtype=np.float32)
    for tail in ("...", "…", ",", ";", ":", "—", "-", "... ", ", "):
        d = D()
        d.asr, d.sound_questions, d.sound_threshold, d.last_sound_q = FakeAsrQ(+5.0), True, -1.0, "x"
        text = f"Мы сначала проверим{tail}"
        got = ask(d, data, text, text)
        check(got is None and not d.asr.asked and d.last_sound_q is None,
              f"a phrase ending on {tail!r} gets no question mark and the recognizer is not asked", str(got))
    d = D()
    d.asr, d.sound_questions, d.sound_threshold, d.last_sound_q = FakeAsrQ(+5.0), True, -1.0, None
    got = ask(d, data, "Мы это уже сделали.", "Мы это уже сделали.")
    check(got == "Мы это уже сделали?", "a finished phrase still gets it when the voice asks", str(got))


def main():
    for fn in (test_hold, test_tap_then_hold, test_resume_in_tail, test_auto_repeat, test_tap_to_stop,
               test_esc, test_toggle, test_busy_waits, test_tail, test_resume_recorder, test_dropped,
               test_xruns, test_cut_off_question):
        print(f"--- {fn.__name__}")
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(False, f"{fn.__name__} crashed", f"{type(exc).__name__}: {exc}")
    print(f"\n{'all passed' if not bad else str(bad) + ' failed'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
