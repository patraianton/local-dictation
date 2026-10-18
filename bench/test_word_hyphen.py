# -*- coding: utf-8 -*-
"""A hyphen between two Russian words, a dash between digits (18.10.2026).

Made-up phrases only.

    ..\\.venv\\Scripts\\python.exe test_word_hyphen.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import word_hyphen  # noqa: E402

CASES = [
    ("Подождём день–два и посмотрим.", "Подождём день-два и посмотрим.", "two words: a hyphen"),
    ("Пять–шесть коробок хватит.", "Пять-шесть коробок хватит.", "at the head of a sentence"),
    ("На трёх–четырёх полках.", "На трёх-четырёх полках.", "with ё"),
    ("Подождём 2–3 дня.", "Подождём 2–3 дня.", "between digits the dash stays"),
    ("Это склад — там всё лежит.", "Это склад — там всё лежит.", "a spaced dash is a dash"),
    ("Сделай план – потом обсудим.", "Сделай план – потом обсудим.", "a spaced short dash is left too"),
    ("Нужен e–mail клиента.", "Нужен e–mail клиента.", "Latin letters are not touched"),
    ("Кто-то уже пришёл.", "Кто-то уже пришёл.", "a hyphen stays a hyphen"),
]


def main() -> int:
    bad = 0
    for said, want, why in CASES:
        got = word_hyphen(said)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {why}: {got}")
    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
