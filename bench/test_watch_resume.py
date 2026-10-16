# -*- coding: utf-8 -*-
"""A restarted watch picks up where the last one stopped (16.10.2026).

No takes, no service: only the resume point is checked, on files in a
temporary folder.

    ..\\.venv\\Scripts\\python.exe test_watch_resume.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
import watch as W  # noqa: E402


def take_id(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d_%H%M%S-000")


def main() -> int:
    bad = 0
    tmp = Path(tempfile.mkdtemp())
    W.STATE, W.ALIVE = tmp / "state", tmp / "alive"
    now_prefix = datetime.now().strftime("%Y-%m-%d_%H%M")

    def check(name, want_last, last):
        nonlocal bad
        got = W.resume_point()
        ok = (got == last) if want_last else got.startswith(now_prefix) and got.endswith("-999")
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    check("no state at all: from now", False, None)

    old = take_id(3)
    W.STATE.write_text(old, encoding="utf-8")
    check("last take 3 h old, no watch ever looked: from now", False, old)

    W.ALIVE.touch()
    check("last take 3 h old, a watch looked a minute ago: from that take", True, old)

    stale = time.time() - 2 * 3600
    os.utime(W.ALIVE, (stale, stale))
    check("last take 3 h old, the last watch looked 2 h ago: from now", False, old)

    fresh = take_id(0.2)
    W.STATE.write_text(fresh, encoding="utf-8")
    check("last take 12 min old: from that take", True, fresh)

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
