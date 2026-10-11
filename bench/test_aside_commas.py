# -*- coding: utf-8 -*-
"""Checks stt.polish.keep_aside_commas: a comma the corrector took away from
around an aside ("короче", "кстати", a swear word) comes back.

Invented phrases.

    ..\\.venv\\Scripts\\python.exe test_aside_commas.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import constrain, keep_aside_commas  # noqa: E402

CASES = [
    # (recognized, corrector's text, expected)
    ("Где, блядь, ключи от склада?", "Где блядь ключи от склада?", "Где, блядь, ключи от склада?"),
    ("Потому что, короче, склад закрыт.", "Потому что короче, склад закрыт.",
     "Потому что, короче, склад закрыт."),
    ("Возьми, кстати, вторую коробку.", "Возьми, кстати вторую коробку.",
     "Возьми, кстати, вторую коробку."),
    # both commas kept: nothing to do
    ("Проверь, пожалуйста, полку.", "Проверь, пожалуйста, полку.", "Проверь, пожалуйста, полку."),
    # the recognizer had no commas: the corrector's choice stands
    ("Это наверное последняя коробка.", "Это, наверное, последняя коробка.",
     "Это, наверное, последняя коробка."),
    ("Это наверное последняя коробка.", "Это наверное последняя коробка.",
     "Это наверное последняя коробка."),
    # a word that only looks like an aside, inside another word: untouched
    ("Отметь, типа, всё.", "Отметь, типажи всё.", "Отметь, типажи всё."),
    # the neighbours changed: not the same place, untouched
    ("Неси, короче, ящик.", "Неси короче ящики.", "Неси короче ящики."),
    # capital in the corrector's text is kept as it is
    ("Ну, Короче, ладно.", "Ну Короче, ладно.", "Ну, Короче, ладно."),
    # two asides in one take
    ("Где, блядь, ключи, кстати, от склада?", "Где блядь ключи кстати от склада?",
     "Где, блядь, ключи, кстати, от склада?"),
]


def main() -> int:
    bad = 0
    for raw, corrected, want in CASES:
        got = keep_aside_commas(raw, corrected)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {want}")
        if not ok:
            print(f"      got {got}")
    # and through the whole lock, as a take goes
    raw, corrected = "Где, блядь, ключи от склада?", "Где блядь ключи от склада?"
    got = constrain(raw, corrected, set())
    ok = got == raw
    bad += not ok
    print(f"[{'v' if ok else 'X'}] through constrain: {got}")
    print(f"\n{len(CASES) + 1 - bad} of {len(CASES) + 1} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
