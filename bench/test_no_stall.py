# -*- coding: utf-8 -*-
"""Checks that a dead corrector never makes the person wait.

Why this exists. Until 2026-08-20 polish() probed LM Studio right on the
dictation path. With LM Studio switched off that probe cost a full connect
timeout, and on this machine a refused localhost connection takes 2.0 s
(measured on five different closed ports). Result: every first take after a
30-second pause took 2.1 s instead of 0.15 s, and the person stared at an
empty cursor. Measured in the real log, 2026-08-19..20: 43 takes, median
total 2189 ms, of which 2055 ms was this probe alone.

No LM Studio needed: the network client is replaced with a fake.

    ..\\.venv\\Scripts\\python.exe test_no_stall.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import Polisher  # noqa: E402

SLOW = 2.0   # what a dead LM Studio costs on this machine


class DeadLmStudio:
    """Every call hangs for SLOW seconds and then fails, like a closed port."""

    def __init__(self):
        self.calls = 0

    def _stall(self):
        self.calls += 1
        time.sleep(SLOW)
        raise TimeoutError("ConnectTimeout")

    def get(self, *a, **kw):
        self._stall()

    def post(self, *a, **kw):
        self._stall()


class Alive:
    """LM Studio that answers, with one chat model loaded."""

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "qwen/qwen3-30b"}],
                    "choices": [{"message": {"content": "ок"}}]}

    def get(self, *a, **kw):
        return self.Reply()

    def post(self, *a, **kw):
        return self.Reply()


def make(min_words=1):
    pol = Polisher({"polish": {"min_words": min_words}}, [], None, set())
    pol._client = DeadLmStudio()
    return pol


def main() -> None:
    bad = 0

    def check(ok, why, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok and detail:
            print(f"      {detail}")

    # 1. the very first take with a dead corrector must not stall
    pol = make()
    t0 = time.perf_counter()
    out, _t, _note = pol.polish("Проверка связи.")
    took = (time.perf_counter() - t0) * 1000
    check(took < 100, "dead corrector: the take is not held up",
          f"waited {took:.0f} ms, allowed under 100")
    check(out == "Проверка связи.", "and the raw text comes back untouched", out)

    # 2. ten takes in a row cost nothing either
    t0 = time.perf_counter()
    for _ in range(10):
        pol.polish("Ещё одна фраза.")
    took = (time.perf_counter() - t0) * 1000
    check(took < 100, "ten takes in a row are still not held up",
          f"waited {took:.0f} ms for all ten")

    # 3. the background probe does run — just off the dictation path
    time.sleep(SLOW + 0.4)
    check(pol._client.calls >= 1,
          "the corrector is still being re-checked, in the background",
          f"probes made: {pol._client.calls}")

    # 4. ...and not more often than every 30 s, so a dead LM Studio
    #    is not hammered
    before = pol._client.calls
    for _ in range(5):
        pol.polish("И ещё.")
    time.sleep(0.3)
    check(pol._client.calls == before,
          "a dead LM Studio is not hammered: no more than one probe per 30 s",
          f"was {before}, became {pol._client.calls}")

    # 5. no second probe starts while the first one is still hanging
    pol2 = make()
    pol2.polish("Раз.")
    time.sleep(0.2)              # the probe is mid-flight, it hangs 2 s
    pol2.polish("Два.")
    pol2.polish("Три.")
    time.sleep(0.2)
    check(pol2._client.calls == 1,
          "probes do not pile up on top of each other",
          f"in flight: {pol2._client.calls}")

    # 6. the reason is reported honestly, not silently swallowed
    time.sleep(SLOW + 0.4)
    check("LM Studio" in pol2.reason,
          "the page and the log still see why the corrector is off", pol2.reason)

    # 7. a phrase shorter than min_words does not even reach the probe
    pol3 = make(min_words=5)
    t0 = time.perf_counter()
    _o, _t, note = pol3.polish("Два слова")
    check((time.perf_counter() - t0) * 1000 < 20 and pol3._client.calls == 0,
          "a short phrase is skipped before any network at all", note)

    # 8. LM Studio started later — the corrector comes back by itself.
    #    The price of moving the check off the hot path: the take that
    #    discovers LM Studio is alive still goes out raw. The one after it
    #    is corrected. That is the whole trade, and it is worth it.
    pol4 = make()
    pol4._client = Alive()                        # LM Studio is up again
    seen = []
    pol4.on_status = lambda ok, model: seen.append(model)
    out, _t, _n = pol4.polish("Пока корректор ещё не знает, что он жив.")
    check(out == "Пока корректор ещё не знает, что он жив.",
          "the take that finds LM Studio alive still goes out raw — no waiting",
          out)
    time.sleep(0.5)
    check(pol4.available and pol4.model == "qwen/qwen3-30b",
          "but by the next take the corrector has picked itself back up",
          f"available={pol4.available}, model={pol4.model!r}")
    check(seen == ["qwen/qwen3-30b"],
          "and it says so once, so the log shows when it came back", str(seen))

    print()
    print("all passed" if not bad else f"{bad} failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
