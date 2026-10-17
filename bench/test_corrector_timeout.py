# -*- coding: utf-8 -*-
"""A long take gets more time from the corrector than a short one (17.10.2026).

No LM Studio needed: the network client is replaced with a fake that records
the timeout it was given.

    ..\\.venv\\Scripts\\python.exe test_corrector_timeout.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import Polisher, corrector_timeout  # noqa: E402
from test_no_stall import Alive  # noqa: E402

SHORT = "Открой почту и проверь письма."
LONG = " ".join(["Сегодня надо разобрать почту, ответить клиентам и закрыть задачи."] * 25)


class Recording(Alive):
    """Alive LM Studio that writes down the timeout of every chat request."""

    def __init__(self):
        super().__init__()
        self.timeouts = []

    def post(self, *a, **kw):
        self.timeouts.append(kw.get("timeout"))
        return super().post(*a, **kw)


def main() -> int:
    bad = 0

    def check(name, ok, got):
        nonlocal bad
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    t_short, t_long = corrector_timeout(4.0, SHORT), corrector_timeout(4.0, LONG)
    check("a short take: about the base", 4.0 < t_short < 4.2, f"{t_short:.2f} s")
    check("a long take (~1,700 characters): several seconds more", 7.5 < t_long < 9.0,
          f"{len(LONG)} characters -> {t_long:.2f} s")
    check("an empty take: the base", corrector_timeout(4.0, "") == 4.0, corrector_timeout(4.0, ""))

    p = Polisher({"polish": {"enabled": True, "timeout_s": 4.0, "min_words": 1}}, [])
    fake = Recording()
    p._client = fake
    p.check(force=True)
    p.polish(SHORT)
    p.polish(LONG)
    sent = [t for t in fake.timeouts if t is not None]
    # One take may ask more than once (a second look at a question): every
    # request of a take carries that take's timeout.
    check("every request carries its take's own timeout",
          len(sent) >= 2 and abs(sent[0] - t_short) < 1e-9 and abs(sent[-1] - t_long) < 1e-9
          and all(min(abs(t - t_short), abs(t - t_long)) < 1e-9 for t in sent),
          [round(t, 2) for t in sent])

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
