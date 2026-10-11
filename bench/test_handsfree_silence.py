# -*- coding: utf-8 -*-
r"""A hands-free take that nobody speaks in must not run for five minutes.

No microphone, no model, no keyboard hooks: the same fakes as test_key_flow.py
(a thread that hands the recorder blocks of 4096 samples at 48 kHz, the key
handlers of Dictation called directly).

What this guards (the case of 01.10.2026: an accidental tap turned hands-free
on, nobody spoke, the take ran to the 300 s safety net with other audio ducked
to 15% and the microphone held; 17 takes of 60+ s with a few words in them
since 10.08):
  1. no voice at all for handsfree_silent_cancel_s -> the take is cancelled
     like Esc (audio given back, a warning that stays on the screen for a
     minute, a log line, a dropped row with the reason "handsfree_silent" and
     the sound kept);
  2. voice, then handsfree_idle_stop_s of none -> an ordinary stop: the take is
     recognized and pasted, nothing is lost, and a notice says it ended by
     itself (nothing records after it);
  3. "voice" is not one click or cough, and voice resets the idle timer;
  4. a take whose key is held is never cut, and a press in hands-free puts the
     rules aside until the key is released;
  5. the watchdog never acts on a take that has already stopped, was cancelled
     with Esc, was resumed, or is another take by now;
  6. it costs nothing, and a failure inside it cannot take the maximum-length
     safety net down, nor leave other audio ducked;
  7. a key whose release was lost (no repeat for KEY_REPEAT_GAP_S) does not
     hold the rules off for good.

    ..\.venv\Scripts\python.exe test_handsfree_silence.py
"""
import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Imported before anything is printed: stt.__main__ swaps the output stream.
from test_key_flow import LOUD, QUIET, Rig, wait_for  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt import audio as A  # noqa: E402
from stt import config as cfg_mod  # noqa: E402

SR = 16000
RNG = np.random.default_rng(11)
bad = 0


def check(ok, why, detail=""):
    global bad
    bad += 0 if ok else 1
    print(f"[{'v' if ok else 'X'}] {why}")
    if not ok and detail:
        print(f"      {detail}")


def noise(seconds, level):
    return (RNG.standard_normal(int(SR * seconds)) * level).astype(np.float32)


def timeline(*parts):
    """parts: (seconds, level) pairs -> one 16 kHz signal."""
    return np.concatenate([noise(s, lv) for s, lv in parts])


def rules45():
    """The rule logic is tested at 45 s / 60 s; the shipped defaults are in test_defaults."""
    return A.VoiceWatch(silent_cancel_s=45.0, idle_stop_s=60.0)


def feed_all(watch, audio, chunk=4000):
    for i in range(0, len(audio), chunk):
        watch.feed(audio[i:i + chunk])


def when(watch, audio, chunk=4000):
    """Feeds in 250 ms polls (what the watchdog does); (verdict, second it came), or (None, None)."""
    for i in range(0, len(audio), chunk):
        watch.feed(audio[i:i + chunk])
        v = watch.verdict()
        if v:
            return v, watch.elapsed_s
    return None, None


SPEECH = 0.05          # the median 0.3 s window of speech on this microphone (0.052, 6,425 takes)
QUIET_SPEECH = 0.008   # the quietest dictations (take medians 0.004-0.011, 13 of 5,307 takes)
ROOM = 0.0012          # the median window of an idle take (0.0006-0.0024)


# ---------------------------------------------------------------- 1. the rules, on a synthetic clock
def test_rules():
    # A: nobody ever speaks. Not a second early, then cancel.
    w = rules45()
    v, t = when(w, timeline((44.0, ROOM)))
    check(v is None, "A: 44 s of room noise: carry on", str(v))
    v, t = when(w, timeline((2.0, ROOM)))
    check(v == "cancel" and 45.0 <= t <= 45.3, "A: cancelled at 45 s of the take's own sound", f"{v} at {t}")
    check(not w.heard, "... and nobody was heard")

    # B: speech for 4 s, then silence: stop 60 s after the last speech.
    w = rules45()
    v, t = when(w, timeline((1.0, ROOM), (4.0, SPEECH), (59.0, ROOM)))
    check(v is None and w.heard, "B: 59 s after the speech: carry on", f"{v} at {t}")
    v, t = when(w, timeline((2.0, ROOM)))
    last = w.last_voice_s
    check(v == "stop" and 4.8 <= last <= 5.4 and 59.9 <= t - last <= 60.4,
          "B: stopped 60 s after the last voice", f"{v} at {t}, last voice {last}")

    # A never fires once there has been voice, and B never fires before voice.
    w = rules45()
    v, _ = when(w, timeline((1.0, SPEECH), (50.0, ROOM)))
    check(v is None, "after voice at 1 s the 45 s rule is gone (50 s of quiet is not yet 60 s)", str(v))

    # A click, a thump or a cough is not voice.
    for what, burst in (("a click (one window)", [(0.3, 0.3)]),
                        ("a thump of 0.6 s", [(0.6, 0.15)]),
                        ("the burst of 01.10.2026 (two loud windows)", [(0.6, 0.035)])):
        w = rules45()
        v, t = when(w, timeline((1.2, ROOM), *burst, (50.0, ROOM)))
        check(v == "cancel" and not w.heard, f"{what} is not voice: the take is still cancelled at 45 s",
              f"{v} at {t}, heard {w.heard}")
    w = rules45()
    v, t = when(w, timeline(*[(5.0, ROOM), (0.3, 0.3)] * 8, (10.0, ROOM)))
    check(v == "cancel" and not w.heard, "eight clicks five seconds apart are not voice either", f"{v} at {t}")
    # Voice counts when it is voice: a sentence, a murmur, a quiet dictation.
    for what, seg in (("a sentence of 2 s", [(2.0, SPEECH)]),
                      ("a murmur at 0.012", [(2.0, 0.012)]),
                      ("a quiet dictation at 0.008", [(3.0, QUIET_SPEECH)]),
                      ("a whisper at 0.0045", [(3.0, 0.0045)])):
        w = rules45()
        feed_all(w, timeline((3.0, ROOM), *seg))
        check(w.heard, f"{what} is voice", f"last voice {w.last_voice_s}")
    w = rules45()
    feed_all(w, timeline((300.0, ROOM)))
    check(not w.heard, "five minutes of an ordinary quiet room are never voice")
    w = rules45()
    feed_all(w, timeline((300.0, 0.0025)))
    check(not w.heard, "nor five minutes of a noisy one (0.0025, the worst idle take's median)")

    # Speech with the pauses of speech in it.
    w = rules45()
    feed_all(w, timeline(*[(0.5, SPEECH), (0.3, ROOM), (0.7, SPEECH), (0.4, ROOM)] * 4))
    check(w.heard and w.last_voice_s > 7.0, "speech with pauses between the words is voice up to its end",
          f"last voice {w.last_voice_s}")

    # Voice resets the idle timer.
    w = rules45()
    sig = timeline((2.0, SPEECH), (50.0, ROOM), (3.0, SPEECH), (59.0, ROOM))
    v, t = when(w, sig)
    check(v is None, "voice again after 50 s: the idle timer starts over", f"{v} at {t}")
    v, t = when(w, timeline((3.0, ROOM)))
    check(v == "stop" and 114.0 <= t <= 115.5, "... and the stop comes 60 s after the SECOND speech",
          f"{v} at {t}")

    # Sound comes in any chunking; the answer is the same.
    sig = timeline((1.0, ROOM), (3.0, SPEECH), (10.0, ROOM), (1.0, 0.3), (70.0, ROOM))
    ref = rules45()
    feed_all(ref, sig, chunk=len(sig))
    for chunk in (1, 777, 4000, 4799, 4801, 12000):
        w = rules45()
        feed_all(w, sig[: 60 * SR], chunk=chunk)
        feed_all(w, sig[60 * SR:], chunk=chunk)
        same = (w.heard, round(w.last_voice_s, 3), w.verdict()) == (ref.heard, round(ref.last_voice_s, 3), ref.verdict())
        check(same, f"fed in pieces of {chunk} samples the answer is the same as in one piece",
              f"{(w.heard, w.last_voice_s, w.verdict())} vs {(ref.heard, ref.last_voice_s, ref.verdict())}")

    # Switched off.
    w = A.VoiceWatch(silent_cancel_s=0, idle_stop_s=60)
    v, t = when(w, timeline((200.0, ROOM)))
    check(v is None and w.enabled, "silent_cancel_s = 0: the 45 s rule is off, the other stays on", str(v))
    w = A.VoiceWatch(silent_cancel_s=45, idle_stop_s=0)
    v, t = when(w, timeline((2.0, SPEECH), (200.0, ROOM)))
    check(v is None, "idle_stop_s = 0: the 60 s rule is off", str(v))
    check(not A.VoiceWatch(0, 0).enabled, "both 0: nothing to watch")

    # Cost: a poll on a take that has been going for five minutes.
    w = rules45()
    feed_all(w, timeline((290.0, ROOM)))
    piece = timeline((0.25, ROOM))
    t0 = time.perf_counter()
    for _ in range(400):
        w.feed(piece)
        w.verdict()
    per = (time.perf_counter() - t0) / 400 * 1000
    check(per < 1.0, "feeding 250 ms of sound and asking costs well under a millisecond", f"{per:.3f} ms")


# ---------------------------------------------------------------- 2. the recorder hands over the take's sound
def test_new_audio():
    rig = Rig()
    rec = rig.rec
    check(rec.new_audio()[0].size == 0, "nothing is handed over while no take is being recorded")
    rig.speak(0.3)                         # the pre-roll is loud...
    time.sleep(0.5)
    rig.speak(QUIET)                       # ...the take itself is not
    rec.start()
    time.sleep(0.6)
    a1, m1 = rec.new_audio(None)
    time.sleep(0.4)
    a2, m2 = rec.new_audio(m1)
    a3, m3 = rec.new_audio(m2)
    out = rec.stop(tail_s=0.0)
    rms = lambda a: float(np.sqrt(np.mean(a * a)))  # noqa: E731
    check(a1.size > 0 and rms(a1) < 0.01, "the pre-roll (loud) is not in what the take hands over",
          f"{a1.size} samples, rms {rms(a1) if a1.size else 0:.4f}")
    check(a3.size == 0 and m3 == m2, "asking again at once hands over nothing and keeps the mark")
    pre = int(rec.last_preroll_s * 16000)
    got = a1.size + a2.size
    check(abs(got - (len(out) - pre)) < 16000 * 0.3,
          "the pieces add up to the take (what stop() returns, less the pre-roll), nothing twice",
          f"pieces {got}, take {len(out)}, pre-roll {pre}")
    check(rec.new_audio(m2)[0].size == 0, "after the stop there is nothing to hand over")
    rig.done()


# ---------------------------------------------------------------- 3. the whole thing, on real time
CURRENT = []     # the Quiet in force; every new rig starts it from a clean slate


class Quiet:
    """Counts what the world would see: dropped rows, log lines."""

    def __init__(self):
        CURRENT[:] = [self]
        self.tmp = Path(tempfile.mkdtemp())
        self.logs = []
        self._old = (cfg_mod.LOG_DIR, cfg_mod.REC_DIR, M.log)
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR = self.tmp / "logs", self.tmp / "rec"
        M.log = self.logs.append

    def rows(self):
        p = self.tmp / "logs" / "dropped" / f"{datetime.now():%Y-%m-%d}.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]

    def reset(self):
        """A clean slate between the cases of one test."""
        shutil.rmtree(self.tmp / "logs", ignore_errors=True)
        shutil.rmtree(self.tmp / "rec", ignore_errors=True)
        self.logs.clear()

    def close(self):
        CURRENT.clear()
        cfg_mod.LOG_DIR, cfg_mod.REC_DIR, M.log = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_rig(cancel_s=1.2, idle_s=1.2, tail_s=0.4):
    if CURRENT:
        CURRENT[0].reset()
    r = Rig(tail_s=tail_s)
    d = r.d
    d.cfg = {"learn": {"keep_audio": True}, "paste": {}}
    d.handsfree_silent_cancel_s, d.handsfree_idle_stop_s = cancel_s, idle_s
    d.handsfree_voice_rms = A.VOICE_RMS
    return r, d


def count_reads(r):
    """Counts how often the watchdog reads the take's sound from the recorder."""
    reads = []
    real = r.rec.new_audio

    def counting(mark=None):
        reads.append(1)
        return real(mark)

    r.rec.new_audio = counting
    return reads


def tap(d, hold=0.05):
    d.on_down()
    time.sleep(hold)
    d.on_up()


def hold(d, seconds, every=0.3):
    """The key held down with the keyboard repeating itself, as it does (call after the first on_down)."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(every)
        d.on_down()


class Escapes:
    """Collects what would end a thread with a traceback (the watchdog has no handler of its own)."""

    def __enter__(self):
        self.seen = []
        self._old = threading.excepthook
        threading.excepthook = lambda args: self.seen.append(args.exc_value)
        return self

    def __exit__(self, *exc):
        threading.excepthook = self._old


def notices(d):
    """Lines the stop rule put on the screen after the text was pasted."""
    return [c for c in d.hud.calls if c[0] == "warn" and c[1].startswith("quiet")]


def test_cancel_when_nobody_speaks():
    q = Quiet()
    try:
        r, d = make_rig()
        r.speak(QUIET)
        t0 = time.perf_counter()
        tap(d)
        check(d.recording and d.locked, "a tap: hands-free")
        check(wait_for(lambda: not d.recording, 4.0), "nobody speaks: the take is cancelled by itself")
        took = time.perf_counter() - t0
        wait_for(lambda: q.rows(), 2.0)    # the row is the last thing it does: after it, everything is done
        check(1.1 <= took <= 2.4, "... after its own 1.2 s (plus a poll), not before", f"{took:.2f} s")
        check(not r.rec.recording, "the recorder is stopped")
        check(d.ducker.ducked == 1 and d.ducker.restored == 1, "other audio is given back, once",
              f"ducked {d.ducker.ducked}, restored {d.ducker.restored}")
        check(("warn", "no speech — cancelled") in d.hud.seen, "the screen says so", str(d.hud.seen))
        shown = [c for c in d.hud.calls if c[1] == "no speech — cancelled"]
        check(len(shown) == 1 and shown[0][2] == M.CANCEL_NOTICE_S and M.CANCEL_NOTICE_S >= 30.0,
              "... and keeps saying so for a minute, not a 2 s flash (nothing records after a cancel)",
              str(shown))
        check(any("nobody spoke" in x for x in q.logs), "service.log says so", str(q.logs))
        wait_for(lambda: q.rows(), 2.0)          # written just after the take is let go
        rows = q.rows()
        check([x["reason"] for x in rows] == ["handsfree_silent"], "a dropped row with the reason handsfree_silent",
              str(rows))
        check(rows and rows[0]["wav"] and Path(rows[0]["wav"]).exists(),
              "the sound is kept (a false cancel must be findable and replayable)")
        check(rows and 0.9 <= rows[0]["seconds"] <= 2.4, "the row carries the take's length", str(rows[0:1]))
        time.sleep(0.5)
        check(len(r.taken) == 0, "nothing is sent to the recognizer")
        check(len(q.rows()) == 1 and d.ducker.restored == 1, "and nothing happens twice")
        # The key works as ever afterwards.
        r.speak(LOUD)
        d.on_down()
        time.sleep(0.4)
        d.on_up()
        r.speak(QUIET)
        check(wait_for(lambda: len(r.taken) == 1) and r.starts == 2, "the next press starts an ordinary take")
        r.done()
    finally:
        q.close()


def test_stop_when_speech_ends():
    q = Quiet()
    try:
        r, d = make_rig()
        r.speak(LOUD)
        t0 = time.perf_counter()
        tap(d)
        time.sleep(1.1)                    # speaks for about a second
        r.speak(QUIET)
        check(d.recording, "while he speaks it goes on")
        check(wait_for(lambda: not d.recording, 5.0), "then silence: the take ends by itself")
        took = time.perf_counter() - t0
        check(2.0 <= took <= 3.8, "... 1.2 s after the speech, not before", f"{took:.2f} s")
        check(wait_for(lambda: len(r.taken) == 1), "the take is handed to the recognizer, as a normal stop")
        check(not q.rows(), "no dropped row: nothing was thrown away", str(q.rows()))
        secs = len(r.taken[0][0]) / 16000
        check(secs >= 2.0, "its sound is whole, the speech and the silence after it", f"{secs:.2f} s")
        check(d.ducker.ducked == 1 and d.ducker.restored == 1, "other audio is given back, once",
              f"ducked {d.ducker.ducked}, restored {d.ducker.restored}")
        check(any("ending the take" in x for x in q.logs), "service.log says so", str(q.logs))
        check(wait_for(lambda: notices(d)), "the screen says the take ended by itself, once the text is in",
              str(d.hud.calls))
        shown = notices(d)
        check(len(shown) == 1 and shown[0][2] == M.STOP_NOTICE_S and shown[0][1] == "quiet 1 s — ended",
              "... for a few seconds, with the length of the quiet that did it", str(shown))
        time.sleep(0.4)
        check(len(r.taken) == 1, "handed over once")
        r.done()

        # Voice again inside the idle time: the clock starts over.
        r, d = make_rig(cancel_s=1.2, idle_s=1.5)
        r.speak(LOUD)
        t0 = time.perf_counter()
        tap(d)
        time.sleep(1.0)
        r.speak(QUIET)
        time.sleep(1.0)                    # 1.0 s of quiet, under 1.5
        r.speak(LOUD)
        time.sleep(1.0)
        r.speak(QUIET)
        check(d.recording, "speech again before the idle time is up keeps the take", f"{time.perf_counter() - t0:.2f} s")
        check(wait_for(lambda: not d.recording, 5.0) and (time.perf_counter() - t0) >= 4.0,
              "and the stop comes only 1.5 s after the second speech", f"{time.perf_counter() - t0:.2f} s")
        check(wait_for(lambda: len(r.taken) == 1), "one take, processed")
        r.done()
    finally:
        q.close()


def test_held_key_is_never_cut():
    q = Quiet()
    try:
        r, d = make_rig(cancel_s=0.8, idle_s=0.8)
        reads = count_reads(r)
        r.speak(QUIET)
        d.on_down()                        # held, and silent: thinking with the key down
        time.sleep(2.5)
        check(d.recording and not d.locked and not q.rows(),
              "a take whose key is held is not cut, however quiet", f"rec {d.recording} rows {q.rows()}")
        check(not reads, "... and its sound is not even read while the key is held", f"{len(reads)} reads")
        d.on_up()
        r.speak(QUIET)
        check(wait_for(lambda: len(r.taken) == 1) and not q.rows(), "its release stops it normally")
        r.done()

        # Not hands-free is enough on its own, whatever the key's bookkeeping says.
        r, d = make_rig(cancel_s=0.8, idle_s=0.8)
        reads = count_reads(r)
        r.speak(QUIET)
        d.on_down()
        d.key_down = False                 # the state of a take whose release was lost and then forgotten
        time.sleep(2.0)
        check(d.recording and not d.locked and not q.rows() and not reads,
              "a take that is not hands-free is never cut and never read, key down or not",
              f"rec {d.recording}, rows {q.rows()}, reads {len(reads)}")
        d.on_esc()
        r.done()

        # A held take that spoke first and then went quiet: the same.
        r, d = make_rig(cancel_s=0.8, idle_s=0.8)
        r.speak(LOUD)
        d.on_down()
        time.sleep(0.8)
        r.speak(QUIET)
        time.sleep(2.0)
        check(d.recording, "held, spoke, then quiet: still not cut")
        d.on_up()
        check(wait_for(lambda: len(r.taken) == 1), "released: processed")
        r.done()

        # A press in hands-free puts the rules aside until the release.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        reads = count_reads(r)
        r.speak(QUIET)
        tap(d)
        time.sleep(0.5)                    # half-way to the cancel
        d.on_down()                        # presses to stop it (or to speak)
        at_press = len(reads)
        hold(d, 2.5)                       # holds far past the limit, silent, the keyboard repeating
        check(len(reads) <= at_press + 1 and at_press >= 1,
              "... the sound is read before the press and not while the key is down",
              f"{at_press} reads at the press, {len(reads)} after")
        check(d.recording and not q.rows() and not r.taken,
              "a press in hands-free: the rules wait while the key is down", f"rec {d.recording} rows {q.rows()}")
        for _ in range(6):                 # the keyboard repeating while he holds
            time.sleep(0.05)
            d.on_down()
        d.on_up()
        check(wait_for(lambda: len(r.taken) == 1), "its release stops the take, as ever")
        check(not q.rows() and d.ducker.restored == 1,
              "it is an ordinary stop: not a dropped take", f"rows {q.rows()}, restored {d.ducker.restored}")
        r.done()

        # Pressed in hands-free with voice having been heard: same, and the take goes on to the recognizer.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        r.speak(LOUD)
        tap(d)
        time.sleep(0.9)
        r.speak(QUIET)
        d.on_down()
        hold(d, 2.0)
        check(d.recording and not r.taken, "after voice, a press holds the idle rule off as well")
        d.on_up()
        check(wait_for(lambda: len(r.taken) == 1), "and its release stops the take")
        r.done()
    finally:
        q.close()


def test_races():
    q = Quiet()
    try:
        # Esc first: the watchdog never cancels again, and nothing is given back twice.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        r.speak(QUIET)
        tap(d)
        time.sleep(0.4)
        d.on_esc()
        check(not d.recording and d.ducker.restored == 1, "Esc cancels a hands-free take")
        time.sleep(2.0)
        check(not q.rows() and d.ducker.restored == 1 and r.starts == 1,
              "the watchdog of that take does nothing after Esc",
              f"rows {q.rows()}, restored {d.ducker.restored}")
        r.done()

        # A stop by the key first: the watchdog does not cancel it, nor the recorder's tail.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0, tail_s=0.6)
        r.speak(QUIET)
        tap(d)
        time.sleep(0.4)
        d.on_down()
        time.sleep(0.05)
        d.on_up()                          # the stop
        check(not d.recording, "a tap stops a hands-free take")
        check(wait_for(lambda: len(r.taken) == 1), "it is processed")
        time.sleep(1.8)
        check(not q.rows() and d.ducker.restored == 1, "and the watchdog leaves it alone",
              f"rows {q.rows()}, restored {d.ducker.restored}")
        r.done()

        # The next take is judged on ITS OWN sound, not on the clock of the one before.
        r, d = make_rig(cancel_s=1.2, idle_s=1.2, tail_s=0.2)
        r.speak(QUIET)
        tap(d)
        time.sleep(0.9)                    # the first take is 0.9 s old: its rule is about to fall
        d.on_down()
        time.sleep(0.05)
        d.on_up()                          # stopped by a tap
        wait_for(lambda: len(r.taken) == 1)
        t1 = time.perf_counter()
        tap(d)                             # a second hands-free take
        check(d.recording and d.locked, "a second take begins")
        time.sleep(0.7)
        check(d.recording and not q.rows(),
              "an old watchdog does not cancel the new take on the old take's clock",
              f"rec {d.recording}, rows {q.rows()}")
        check(wait_for(lambda: not d.recording, 4.0) and time.perf_counter() - t1 >= 1.1,
              "the new take is cancelled on its own 1.2 s", f"{time.perf_counter() - t1:.2f} s")
        wait_for(lambda: q.rows(), 2.0)    # the row is written just after the take is let go
        time.sleep(0.3)
        check(len(q.rows()) == 1, "exactly once", str(q.rows()))
        r.done()

        # A press during the tail resumes the take, but as a held one: never cut.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0, tail_s=0.7)
        r.speak(LOUD)
        tap(d)
        time.sleep(0.5)
        d.on_down()
        time.sleep(0.04)
        d.on_up()                          # stop: the tail starts, he is still speaking so it runs on
        time.sleep(0.15)
        d.on_down()                        # pressed inside the tail: the take continues
        check(d.recording and not d.locked, "a press in the tail resumes the take (held, not hands-free)")
        r.speak(QUIET)
        time.sleep(2.5)
        check(d.recording and not q.rows() and not r.taken,
              "a resumed take is held: nobody cuts it", f"rec {d.recording}, rows {q.rows()}")
        d.on_up()
        check(wait_for(lambda: len(r.taken) == 1) and r.starts == 1, "its release ends it: one take")
        r.done()

        # Resumed by the toggle button, which locks it again: the same take, the same listener.
        r, d = make_rig(cancel_s=2.0, idle_s=1.0, tail_s=0.7)
        r.speak(LOUD)
        d.on_toggle()
        time.sleep(1.5)                    # long enough for three windows of voice to be counted
        voice = d._voice
        d.on_toggle()                      # stop, tail runs because the sound is loud
        time.sleep(0.15)
        d.on_toggle()                      # pressed in the tail
        check(d.recording and d.locked and d._voice is voice and voice.heard,
              "a take resumed by the toggle button keeps what was heard")
        r.speak(QUIET)
        check(wait_for(lambda: not d.recording, 5.0), "and goes on under the rules (voice, then silence: stop)")
        check(wait_for(lambda: len(r.taken) == 1) and not q.rows() and r.starts == 1,
              "one take, processed, nothing dropped")
        r.done()

        # The rule has decided but the world moved on first: it must not act.
        class Scripted:
            enabled = True
            silent_cancel_s, idle_stop_s = 45.0, 60.0

            def __init__(self, verdict, before):
                self.verdict, self.before = verdict, before

            def poll(self, recorder):
                self.before()
                return self.verdict

        def fresh():
            r, d = make_rig(cancel_s=45, idle_s=60, tail_s=0.2)
            r.speak(QUIET)
            tap(d)
            return r, d

        for verdict in ("cancel", "stop"):
            r, d = fresh()
            d._voice = Scripted(verdict, d.on_esc)
            got = d._hands_free_idle(d._take_started)
            check(got is False and not q.rows() and d.ducker.restored == 1 and not r.taken,
                  f"{verdict}: Esc came between the check and the act -> nothing more happens",
                  f"got {got}, rows {q.rows()}, restored {d.ducker.restored}")
            r.done()

            r, d = fresh()
            d._voice = Scripted(verdict, d.stop_and_process)
            got = d._hands_free_idle(d._take_started)
            wait_for(lambda: len(r.taken) == 1)
            time.sleep(0.3)
            check(got is False and not q.rows() and len(r.taken) == 1 and d.ducker.restored == 1,
                  f"{verdict}: the take was stopped meanwhile -> it is processed once, not cancelled",
                  f"got {got}, rows {q.rows()}, takes {len(r.taken)}, restored {d.ducker.restored}")
            r.done()

            r, d = fresh()
            d._voice = Scripted(verdict, d.on_down)
            got = d._hands_free_idle(d._take_started)
            check(got is False and d.recording and d.key_down and not q.rows(),
                  f"{verdict}: a key press came between the check and the act -> the key wins",
                  f"got {got}, recording {d.recording}, rows {q.rows()}")
            d.on_up()
            check(wait_for(lambda: len(r.taken) == 1) and not q.rows(), f"{verdict}: and its release stops it normally")
            r.done()

            r, d = fresh()
            started = d._take_started

            def another_take():
                d._take_started = started + 5.0

            d._voice = Scripted(verdict, another_take)
            got = d._hands_free_idle(started)
            check(got is False and d.recording and not q.rows(),
                  f"{verdict}: it was looking at a take that is over -> it leaves this one alone",
                  f"got {got}, recording {d.recording}, rows {q.rows()}")
            r.done()

            r, d = fresh()
            d._voice = Scripted(verdict, lambda: setattr(d, "_voice", A.VoiceWatch()))
            got = d._hands_free_idle(d._take_started)
            check(got is False and d.recording and not q.rows(),
                  f"{verdict}: the listener was replaced (a new take) -> the old verdict is void",
                  f"got {got}, recording {d.recording}")
            r.done()

        # A failure inside the check does not take the safety net with it.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        d.max_seconds = 1

        class Broken(Scripted):
            def poll(self, recorder):
                raise ValueError("boom")

        r.speak(LOUD)
        tap(d)
        d._voice = Broken(None, None)
        check(wait_for(lambda: not d.recording, 4.0) and wait_for(lambda: len(r.taken) == 1),
              "a check that raises is switched off for the take, and the maximum-length net still fires",
              str(q.logs[-3:]))
        check(any("silence check failed" in x for x in q.logs) and d._voice is None, "and it says so")
        r.done()

        # Both rules off: the recorder is never even asked.
        r, d = make_rig(cancel_s=0, idle_s=0)
        asked = []
        real = r.rec.new_audio
        r.rec.new_audio = lambda mark=None: (asked.append(1), real(mark))[1]
        r.speak(QUIET)
        tap(d)
        time.sleep(1.5)
        check(d.recording and d._voice is None and not asked and not q.rows(),
              "both rules at 0: no listener, no reading of the sound, the take is left alone",
              f"voice {d._voice}, reads {len(asked)}")
        d.on_toggle()
        r.done()
    finally:
        q.close()


def test_lost_release():
    """A press whose release never came must not keep the rules off for good (the key looks held, but no repeat)."""
    q = Quiet()
    try:
        # Hands-free, nobody speaks; the press that was to stop it lost its release.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        r.speak(QUIET)
        tap(d)
        d.on_down()                                    # presses to stop it...
        d._down_at -= M.KEY_REPEAT_GAP_S + 1.0         # ...and no repeat comes, nor the release
        check(d.key_down and d.recording and d.locked, "a press in hands-free whose release is lost: key 'down', take on")
        check(wait_for(lambda: not d.recording, 4.0), "the silence rule still ends the take (it used to wait for max_seconds)")
        wait_for(lambda: q.rows(), 2.0)
        check([x["reason"] for x in q.rows()] == ["handsfree_silent"] and d.ducker.restored == 1,
              "cancelled like any other, other audio given back once", f"{q.rows()}, restored {d.ducker.restored}")
        check(any("release was lost" in x for x in q.logs), "and service.log says the release was lost", str(q.logs))
        d.on_down()                                    # the next press: seen as new, not as a repeat
        time.sleep(0.05)
        d.on_up()
        check(d.recording and r.starts == 2, "the key works as ever afterwards", f"rec {d.recording}, starts {r.starts}")
        d.on_esc()
        r.done()

        # The same after speech: the stop rule, and the text still reaches the recognizer.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        r.speak(LOUD)
        tap(d)
        time.sleep(1.1)
        r.speak(QUIET)
        d.on_down()
        d._down_at -= M.KEY_REPEAT_GAP_S + 1.0
        check(wait_for(lambda: not d.recording, 5.0) and wait_for(lambda: len(r.taken) == 1),
              "after speech, a lost release does not stop the quiet rule either: the take is processed")
        check(not q.rows() and d.ducker.restored == 1, "nothing dropped, audio given back once",
              f"{q.rows()}, restored {d.ducker.restored}")
        r.done()

        # The helper itself.
        r, d = make_rig()
        check(not d._key_is_held(), "no key down: not held")
        d.key_down, d._down_at = True, time.perf_counter()
        check(d._key_is_held(), "down, repeating a moment ago: held")
        d._down_at = time.perf_counter() - M.KEY_REPEAT_GAP_S - 0.1
        check(not d._key_is_held(), "down, no repeat for longer than KEY_REPEAT_GAP_S: not held")
        d.key_down = False
        r.done()
    finally:
        q.close()


def test_failures():
    """Whatever breaks while the rule acts, other audio comes back and the thread does not die."""
    q = Quiet()
    try:
        with Escapes() as esc:
            # The recorder fails to stop inside the cancel.
            r, d = make_rig(cancel_s=1.0, idle_s=1.0)
            r.speak(QUIET)
            real_stop = r.rec.stop

            def boom(*a, **k):
                raise MemoryError("boom")

            tap(d)
            r.rec.stop = boom
            check(wait_for(lambda: not d.recording, 4.0), "the recorder fails to stop: the take is over all the same")
            time.sleep(0.3)
            check(d.ducker.restored == 1, "other audio is given back although the recorder failed",
                  f"ducked {d.ducker.ducked}, restored {d.ducker.restored}")
            check(any("failed to stop" in x for x in q.logs), "and service.log says so", str(q.logs))
            check(not esc.seen, "nothing escaped from the watchdog thread", str(esc.seen))
            check(not q.rows(), "no sound to keep, no row")
            r.rec.stop = real_stop
            r.done()

            # Esc, same failure: the same guarantee (the code is shared).
            r, d = make_rig(cancel_s=0, idle_s=0)
            r.speak(QUIET)
            real_stop = r.rec.stop
            tap(d)
            r.rec.stop = boom
            d.on_esc()
            check(not d.recording and d.ducker.restored == 1, "Esc with a failing recorder: audio given back, take over")
            r.rec.stop = real_stop
            r.done()

            # The screen fails right after the take was taken apart: the sound is still kept.
            r, d = make_rig(cancel_s=1.0, idle_s=1.0)
            r.speak(QUIET)
            tap(d)

            def broken_hud(*a, **k):
                raise RuntimeError("no screen")

            d.hud.set = broken_hud
            check(wait_for(lambda: not d.recording, 4.0), "the screen fails during a cancel: the take is over")
            wait_for(lambda: q.rows(), 2.0)
            check([x["reason"] for x in q.rows()] == ["handsfree_silent"] and d.ducker.restored == 1,
                  "the sound is still kept and the audio given back, once", f"{q.rows()}, restored {d.ducker.restored}")
            check(any("failed while acting" in x for x in q.logs) and d._voice is None,
                  "the failure is logged and the rule is off for this take", str(q.logs))
            check(not esc.seen, "nothing escaped", str(esc.seen))
            r.done()

            # The stop fails before anything was taken apart: the rule is off, the safety net is not.
            r, d = make_rig(cancel_s=1.0, idle_s=1.0)
            d.max_seconds = 4

            def refuse(*a, **k):
                raise ValueError("cannot stop")

            r.speak(LOUD)
            real_stop_and_process = d.stop_and_process
            d.stop_and_process = refuse
            tap(d)
            time.sleep(1.1)
            r.speak(QUIET)
            check(wait_for(lambda: d._voice is None, 4.0), "a stop that raises: the rule is off for the take",
                  str(q.logs[-3:]))
            d.stop_and_process = real_stop_and_process
            check(d.recording and any("failed while acting" in x for x in q.logs),
                  "the take is still recording and the failure is logged", f"rec {d.recording}, {q.logs[-2:]}")
            n = sum("failed while acting" in x for x in q.logs)
            time.sleep(0.8)
            check(sum("failed while acting" in x for x in q.logs) == n, "it does not repeat every 0.25 s")
            check(wait_for(lambda: not d.recording, 6.0) and wait_for(lambda: len(r.taken) == 1),
                  "and the maximum-length safety net still ends the take")
            check(not esc.seen, "nothing escaped", str(esc.seen))
            r.done()
    finally:
        q.close()


def test_notices():
    q = Quiet()
    try:
        # A tap that stops the take by hand says nothing extra.
        r, d = make_rig(cancel_s=5.0, idle_s=5.0)
        r.speak(LOUD)
        tap(d)
        time.sleep(0.4)
        tap(d)
        check(wait_for(lambda: len(r.taken) == 1), "a take stopped by the key is processed")
        time.sleep(0.3)
        check(not notices(d), "and says nothing about quiet", str(d.hud.calls))
        r.done()

        # A stop by the rule is announced only if the text was pasted.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        d.process = lambda data, preroll_s=None, xruns=0, xrun_note="": (r.taken.append(data), False)[1]
        r.speak(LOUD)
        tap(d)
        time.sleep(1.1)
        r.speak(QUIET)
        check(wait_for(lambda: len(r.taken) == 1, 5.0), "a take ended by the quiet rule goes to process")
        time.sleep(0.4)
        check(not notices(d), "nothing was pasted: no 'ended' line over whatever process() showed", str(d.hud.calls))
        r.done()

        # Never over the dot of a take that is being recorded.
        r, d = make_rig()
        r.speak(LOUD)
        d.on_down()
        time.sleep(0.1)
        n = len(d.hud.calls)
        d._announce("quiet 60 s — ended", 4.0)
        check(len(d.hud.calls) == n, "a notice is not put on the screen while a take is being recorded")
        d.on_esc()
        d._announce("quiet 60 s — ended", 4.0)
        check(d.hud.calls[-1] == ("warn", "quiet 60 s — ended", 4.0), "and is, once nothing is recording")
        r.done()

        # A stop that a press in the tail took back (the take goes on) is not announced.
        r, d = make_rig()
        r.rec.stop = lambda *a, **k: None
        d._finish(None, "quiet 60 s — ended")
        check(not notices(d) and not r.taken, "a stop that was taken back gives no notice and hands nothing over")
        r.done()
    finally:
        q.close()


# ---------------------------------------------------------------- 4. the take's own clock, at the real defaults
def test_audio_clock():
    """90 s and 120 s (the defaults) are seconds of SOUND: delivered fast they fire fast, not delivered they never fire."""
    q = Quiet()
    try:
        def deliver(r, seconds, level):
            for _ in range(int(seconds * 48000 / 4096)):
                r.rec._callback((RNG.standard_normal(4096) * level).astype(np.float32), 4096, None, None)

        r, d = make_rig(cancel_s=M.Dictation.handsfree_silent_cancel_s, idle_s=M.Dictation.handsfree_idle_stop_s)
        check(d.handsfree_silent_cancel_s == 90.0 and d.handsfree_idle_stop_s == 120.0, "the real defaults")
        r.rec.hot_s = 0.0                  # nothing here may try to reopen a microphone
        r.speak(QUIET)
        tap(d)
        r.mic.halt()
        deliver(r, 85.0, 0.0005)
        time.sleep(0.8)
        check(d.recording and not q.rows(), "85 s of sound, delivered in a flash, is not yet 90 s")
        deliver(r, 7.0, 0.0005)
        check(wait_for(lambda: not d.recording, 3.0), "92 s of sound cancel the take at once, wall clock or not")
        wait_for(lambda: q.rows(), 2.0)
        rows = q.rows()
        check(len(rows) == 1 and rows[0]["reason"] == "handsfree_silent" and 89.5 <= rows[0]["seconds"] <= 93.5,
              "and the row says 90 s of sound", str(rows))
        r.rec.close()

        # Speech, then 118 s, then 122 s of silence.
        r, d = make_rig(cancel_s=M.Dictation.handsfree_silent_cancel_s, idle_s=M.Dictation.handsfree_idle_stop_s)
        r.rec.hot_s = 0.0
        r.speak(QUIET)
        tap(d)
        r.mic.halt()
        deliver(r, 3.0, 0.2)
        deliver(r, 118.0, 0.0005)
        time.sleep(0.8)
        check(d.recording and not r.taken, "3 s of speech, then 118 s of quiet: carry on")
        deliver(r, 5.0, 0.0005)
        check(wait_for(lambda: not d.recording, 3.0) and wait_for(lambda: len(r.taken) == 1),
              "120 s after the speech the take is stopped and goes to the recognizer")
        check(not q.rows() and len(r.taken[0][0]) / 16000 > 120, "nothing dropped, the whole sound is in it",
              f"{len(r.taken[0][0]) / 16000:.1f} s")
        r.rec.close()

        # A stream that delivers nothing does not run the clock.
        r, d = make_rig(cancel_s=1.0, idle_s=1.0)
        r.rec.hot_s = 0.0
        r.speak(QUIET)
        tap(d)
        r.mic.halt()
        time.sleep(3.0)
        check(d.recording and not q.rows(), "a stalled stream (no sound arriving) is not 'no voice for 1 s'")
        d.on_esc()
        r.rec.close()
    finally:
        q.close()


# ---------------------------------------------------------------- 5. defaults
def test_defaults():
    import inspect

    check(M.Dictation.handsfree_silent_cancel_s == 90.0 and M.Dictation.handsfree_idle_stop_s == 120.0,
          "the defaults are 90 s and 120 s (45 would have cut a real dictation that began 130 s after its tap; the longest pause inside a real one was 61-81 s)")
    w = A.VoiceWatch()
    check((w.silent_cancel_s, w.idle_stop_s) == (90.0, 120.0)
          and (A.SILENT_CANCEL_S, A.IDLE_STOP_S) == (90.0, 120.0),
          "the listener itself has the same defaults, so a bare VoiceWatch() cannot disagree with Dictation")
    v, t = when(w, timeline((89.0, ROOM)))
    check(v is None, "defaults: 89 s with nobody speaking: carry on", str(v))
    v, t = when(w, timeline((2.0, ROOM)))
    check(v == "cancel" and 90.0 <= t <= 90.3, "defaults: cancelled at 90 s", f"{v} at {t}")
    src = inspect.getsource(M.Dictation.__init__)
    check('"handsfree_silent_cancel_s"' in src and '"handsfree_idle_stop_s"' in src
          and '"handsfree_voice_rms"' in src, "all three are read from [hotkey] in config.toml")
    check(A.VOICE_WINDOW_S == 0.3 and A.VOICE_NEED == 3 and A.VOICE_SPAN == 4 and A.VOICE_RMS == 0.004,
          "the voice rule is 3 of the last 4 windows of 0.3 s at rms 0.004",
          str((A.VOICE_WINDOW_S, A.VOICE_NEED, A.VOICE_SPAN, A.VOICE_RMS)))
    # An old config without the keys, and one that turns a rule off.
    d = object.__new__(M.Dictation)
    check(d._new_voice() is not None, "a Dictation with no settings gets a listener from the defaults")
    d.handsfree_silent_cancel_s = d.handsfree_idle_stop_s = 0.0
    check(d._new_voice() is None, "both 0: no listener")


def main():
    M.paste_mod.foreground_exe = lambda: "test.exe"
    for fn in (test_rules, test_new_audio, test_cancel_when_nobody_speaks, test_stop_when_speech_ends,
               test_held_key_is_never_cut, test_races, test_lost_release, test_failures, test_notices,
               test_audio_clock, test_defaults):
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
