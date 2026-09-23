# -*- coding: utf-8 -*-
"""The microphone is only handed over to a program that actually records.

Why this exists. Until 31.08.2026 a program from [mic] yield_to took the
microphone away for merely having its window in front. Anton keeps Slack and
Telegram in front all day and dictates INTO them, and the cost was measured in
the log of 31.08: 36 takes (21% of the day) came less than two minutes after
the previous one and still had an empty pre-roll — the half second from before
the key press that carries the first syllable. Slack had not recorded a single
second since 13.08; Telegram one minute at 09:51.

Since the capture is exclusive (WDM-KS: while dictation holds the microphone
nobody else can open it — measured the same day, "Device unavailable"), a
listed program still has to be given a gap to start a call in. Hence the probe
window, and hence this test:

    ..\\.venv\\Scripts\\python.exe test_mic_yield_probe.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import miclisteners  # noqa: E402

FAIL = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got}, want {want}")
    if not ok:
        FAIL.append(name)


class FakeApp:
    """A stand-in for Dictation with only what _may_hold_mic touches."""

    def __init__(self, front: str, recording: set):
        self.yield_to = {"zoom.exe", "slack.exe", "telegram.exe"}
        self.yield_probe_s = 2.5
        self.yield_probe_every_s = 25.0
        self.yield_probe_hot_every_s = 4.0
        self.yield_known_s = 86400.0
        self.yield_grace_s = 120.0
        self._front_exe = ""
        self._front_since = 0.0
        self._probe_until = 0.0
        self.front = front
        self.recording = recording
        # Programs that have recorded on this machine at some point — they get
        # their chance far more often than one that never has.
        self.known = set(recording)
        self.clock = 1000.0

    # The real method, lifted in with the two outside calls replaced.
    def may_hold(self) -> bool:
        if not self.yield_to:
            return True
        now = self.front.lower()
        if now not in self.yield_to:
            self._front_exe = now
            return True
        clock = self.clock
        try:
            if now in self.recording:
                self._front_exe = now
                self._front_since = clock
                self._probe_until = clock + self.yield_probe_s
                return False
        except Exception:
            return False
        if now != self._front_exe:
            self._front_exe = now
            self._front_since = clock
            self._probe_until = clock + self.yield_probe_s
            return False
        if clock < self._probe_until:
            return False
        every = self.yield_probe_every_s
        if now in self.known:
            every = self.yield_probe_hot_every_s
        if clock - self._probe_until >= every:
            self._probe_until = clock + self.yield_probe_s
            return False
        return True


print("--- Slack in front all day, never records ---")
app = FakeApp("slack.exe", recording=set())
check("first moment in front: mic handed over (a call might be starting)",
      app.may_hold(), False)
app.clock += 1.0
check("one second later: still its chance", app.may_hold(), False)
app.clock += 2.0
check("after the probe window: mic taken back", app.may_hold(), True)
app.clock += 10.0
check("ten seconds on: still ours", app.may_hold(), True)
app.clock += 20.0
check("half a minute on: one more chance is offered", app.may_hold(), False)
app.clock += 3.0
check("and taken back again", app.may_hold(), True)

print()
print("--- a real Zoom call ---")
app = FakeApp("zoom.exe", recording={"zoom.exe"})
check("call running: microphone stays with Zoom", app.may_hold(), False)
app.clock += 600.0
check("ten minutes in: still with Zoom", app.may_hold(), False)
app.recording = set()          # звонок кончился, но Zoom остаётся звонилкой
app.clock += 1.0
check("call ended, probe still open: still with Zoom", app.may_hold(), False)
app.clock += 5.0
check("after the probe: dictation takes it back", app.may_hold(), True)

print()
print("--- a program that does record gets its chance far more often ---")
# Zoom sitting in front between calls: the next call must be able to start
# within a few seconds, not wait out the half-minute a chat window gets.
app = FakeApp("zoom.exe", recording=set())
app.known = {"zoom.exe"}       # it recorded on this machine earlier today
app.may_hold()                 # its chance on coming to the front
app.clock += 3.0
check("probe over: dictation holds the mic", app.may_hold(), True)
app.clock += 4.5
check("a few seconds later Zoom is offered it again", app.may_hold(), False)

quiet = FakeApp("slack.exe", recording=set())   # never recorded here
quiet.may_hold()
quiet.clock += 3.0
quiet.may_hold()
quiet.clock += 4.5
check("Slack in the same spot is not offered anything", quiet.may_hold(), True)

print()
print("--- a window nobody yields to ---")
app = FakeApp("chrome.exe", recording=set())
check("browser in front: microphone stays with dictation", app.may_hold(), True)

print()
print("--- switching between two listed programs ---")
app = FakeApp("slack.exe", recording=set())
app.may_hold()
app.clock += 5.0
check("slack settled: ours", app.may_hold(), True)
app.front = "telegram.exe"
check("switched to Telegram: it gets its own chance", app.may_hold(), False)
app.clock += 3.0
check("Telegram did nothing with it: ours again", app.may_hold(), True)

print()
print("--- reading the live registry (Windows) ---")
t0 = time.perf_counter()
live = miclisteners.recording_now()
recent = miclisteners.recorded_within(3600)
ms = (time.perf_counter() - t0) * 1000
print(f"recording right now: {sorted(live) or 'nobody'}")
print(f"recorded within the hour: {sorted(recent)[:8] or 'nobody'}")
print(f"cost: {ms:.1f} ms (called from the keeper thread four times a second)")
if ms > 200:
    FAIL.append("registry read too slow")
    print("FAIL  the registry read is too slow for the keeper thread")

# The name mapping is what ties a window's exe to a registry key.
slack_key = miclisteners._names_of(
    r"C:#Users#user#AppData#Local#slack#app-4.51.191#slack.exe")
tg_key = miclisteners._names_of("TelegramMessengerLLP.TelegramDesktop_t4vj0pshhgkwm")
zoom_key = miclisteners._names_of(r"C:#Users#user#AppData#Roaming#Zoom#bin#Zoom.exe")
check("desktop program", miclisteners._matches("slack.exe", slack_key), True)
check("Store app (Telegram is filed under its package name)",
      miclisteners._matches("Telegram.exe", tg_key), True)
check("Zoom", miclisteners._matches("Zoom.exe", zoom_key), True)
check("no false match between programs",
      miclisteners._matches("chrome.exe", slack_key | tg_key | zoom_key), False)
check("a short name never matches loosely",
      miclisteners._matches("ms.exe", tg_key), False)

print()
print("FAILED: " + ", ".join(FAIL) if FAIL else "all good")
sys.exit(1 if FAIL else 0)
