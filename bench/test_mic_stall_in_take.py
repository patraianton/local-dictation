# -*- coding: utf-8 -*-
"""The microphone dies in the middle of a take — the take goes on (17.10.2026).

Twice in four minutes the stream stopped delivering right at the key press;
the whole press was recorded into nothing and the dead stream was noticed only
after the key went up. Checked here, on the fake sound card of
test_mic_replug.py:

  1. A stream that dies under a take is replaced within a second, and the take
     keeps the sound from before and gets the sound from after.
  2. A stream that dies and cannot be opened again (the microphone pulled out)
     still hands over what was recorded before it died.
  3. A healthy take is left alone.

    ..\\.venv\\Scripts\\python.exe test_mic_stall_in_take.py
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_mic_replug as R  # noqa: E402


def share(data: np.ndarray, level: float) -> float:
    """Seconds of the 16 kHz take at about this level."""
    return float(np.sum(np.abs(data - level) < 0.02)) / 16000.0


def main() -> int:
    ok = []

    # --- 1. Dies under the take, opens again: before and after are both there ---
    logs, stalls = [], []
    sd = R.FakeSD(R.podmic_table(), default=2)
    rec = R.make(sd, hot_s=30.0, logs=logs)
    rec.on_stall = stalls.append
    if not rec.warm():
        R.fail(f"did not open: {rec.last_error}")
    time.sleep(0.6)
    rec._stream.value = 0.3
    rec.start()
    time.sleep(0.4)
    dead = rec._stream
    dead._stop.set()                       # no more blocks, no error: as on 17.10
    R.wait_for(lambda: rec._stream is not None and rec._stream is not dead,
               2.0, "a new stream under the take")
    rec._stream.value = 0.6
    time.sleep(0.5)
    data = rec.stop()
    before, after = share(data, 0.3), share(data, 0.6)
    if before < 0.3 or after < 0.4:
        R.fail(f"the take lost sound: {before:.2f} s before the stall, {after:.2f} s after")
    if len(stalls) != 1 or not (rec.STALL_IN_TAKE_S <= stalls[0] < 1.5):
        R.fail(f"the stall was not reported once with its gap: {stalls}")
    if not any("opened it again" in m for m in logs):
        R.fail(f"no log line about the stream opened again: {logs}")
    ok.append(f"died under the take, new stream after {stalls[0]:.2f} s: "
              f"{before:.2f} s before and {after:.2f} s after are in the take")

    # --- 2. Dies and cannot be opened again: what was recorded is not thrown away ---
    logs, stalls = [], []
    sd = R.FakeSD(R.podmic_table(), default=2)
    rec = R.make(sd, hot_s=30.0, logs=logs)
    rec.on_stall = stalls.append
    rec.warm()
    time.sleep(0.6)
    rec._stream.value = 0.3
    rec.start()
    time.sleep(0.4)
    sd.unplug(43, 30)                      # blocks stop and nothing opens
    time.sleep(1.5)
    if rec._stream is not None:
        R.fail(f"opened something else without the microphone: {rec.recipe}")
    data = rec.stop()
    if share(data, 0.3) < 0.3:
        R.fail(f"the sound from before the stall was thrown away: {data.size} samples")
    if stalls or not any("does not open again" in m for m in logs):
        R.fail(f"a failed reopen was reported wrong: stalls={stalls}, logs={logs}")
    ok.append(f"died and stayed dead: {share(data, 0.3):.2f} s from before it handed over")

    # --- 3. A healthy take is left alone ---
    logs, stalls = [], []
    sd = R.FakeSD(R.podmic_table(), default=2)
    rec = R.make(sd, hot_s=30.0, logs=logs)
    rec.on_stall = stalls.append
    rec.warm()
    time.sleep(0.6)
    rec._stream.value = 0.5
    first = rec._stream
    rec.start()
    time.sleep(1.5)
    data = rec.stop()
    if stalls or rec._stream is not first or share(data, 0.5) < 1.4:
        R.fail(f"a healthy take was touched: stalls={stalls}, same stream={rec._stream is first}")
    ok.append("a healthy 1.5 s take: same stream, nothing reported")

    print("[v] " + "\n[v] ".join(ok))
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
