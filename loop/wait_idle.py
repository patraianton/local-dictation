# -*- coding: utf-8 -*-
"""Waits until nobody has dictated for N minutes (default 10), then exits.

A restart leaves a few seconds in which nothing is recorded, so the app is
restarted only after this returns.

    python loop/wait_idle.py [minutes]
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent.parent


def newest_take() -> str:
    journals = sorted((APP / "logs").glob("????-??-??.jsonl"))
    last = ""
    if journals:
        for line in journals[-1].read_text(encoding="utf-8").splitlines():
            try:
                last = max(last, json.loads(line)["id"])
            except (ValueError, KeyError):
                pass
    return last


def main() -> None:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
    while True:
        last = newest_take()
        age = (datetime.now() - datetime.strptime(last[:17], "%Y-%m-%d_%H%M%S")).total_seconds() \
            if last else 1e9
        if age > minutes * 60:
            print(f"idle {age / 60:.1f} min, last take {last}")
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
