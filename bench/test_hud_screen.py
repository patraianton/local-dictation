# -*- coding: utf-8 -*-
"""Which screen the recording dot goes on (17.10.2026).

Made-up screen layouts; nothing is shown on the screen.

    ..\\.venv\\Scripts\\python.exe test_hud_screen.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.hud import Hud, pick_screen, work_areas  # noqa: E402

MAIN = (0, 0, 2560, 1400)
LEFT = (-1920, 100, 0, 1140)
ABOVE = (0, -1080, 1920, 0)
THREE = [(False, LEFT), (True, MAIN), (False, ABOVE)]

CASES = [
    ("left: the leftmost of three", THREE, "left", LEFT),
    ("right: the rightmost of three", THREE, "right", MAIN),
    ("primary: the main one, wherever it is listed", THREE, "primary", MAIN),
    ("an unknown word: the main one", THREE, "middle", MAIN),
    ("left with one screen: that screen", [(True, MAIN)], "left", MAIN),
    ("no main one reported: the first", [(False, LEFT), (False, ABOVE)], "primary", LEFT),
    ("Windows said nothing: the old way", [], "left", None),
]


def main() -> int:
    bad = 0
    for name, areas, which, want in CASES:
        got = pick_screen(areas, which)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    ok = Hud({"hud": {"screen": "Left"}}).screen == "left" and Hud({}).screen == "primary"
    bad += not ok
    print(f"[{'v' if ok else 'X'}] the setting is read, «primary» when absent")

    found = work_areas()
    ok = bool(found) and sum(main for main, _ in found) == 1
    bad += not ok
    print(f"[{'v' if ok else 'X'}] this machine: {len(found)} screen(s), one of them the main one")

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
