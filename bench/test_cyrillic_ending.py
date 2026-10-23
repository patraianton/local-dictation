# -*- coding: utf-8 -*-
"""A Russian case ending after a Latin term is written in Cyrillic (23.10.2026).

The corrector puts a term in with its ending already attached, and sometimes
types that ending in Latin letters drawn like the Cyrillic ones ("Vercel'e").
It reads the same and is no longer found by search. English endings ("it's",
"don't") are left alone. Invented phrases only.

    ..\\.venv\\Scripts\\python.exe test_cyrillic_ending.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import cyrillic_ending  # noqa: E402

CASES = [
    ("a Latin «e» after a term", "Собери сайт на Vercel'e сегодня.", "Собери сайт на Vercel'е сегодня."),
    ("a Latin «a» and «y»", "Нет доступа Supabase'a, и к Notion'y тоже.",
     "Нет доступа Supabase'а, и к Notion'у тоже."),
    ("already Cyrillic: untouched", "Залей на Vercel'е и в GitHub'е.", "Залей на Vercel'е и в GitHub'е."),
    ("English in a Russian take: untouched", "Он сказал it's fine и don't worry.",
     "Он сказал it's fine и don't worry."),
    ("an English take: untouched", "The cafe'a menu is new.", "The cafe'a menu is new."),
]


def main() -> int:
    bad = 0
    for name, text, want in CASES:
        got = cyrillic_ending(text)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got!r}")
    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
