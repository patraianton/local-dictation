# -*- coding: utf-8 -*-
"""Quotes the corrector put in and the lock left broken go (10.10.2026).

Invented phrases only. Neither a microphone nor LM Studio is needed.

    ..\\.venv\\Scripts\\python.exe test_quotes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import balance_quotes  # noqa: E402

# (what, what the recognizer heard, what the lock left, what must be pasted)
CASES = [
    ("two closing straight quotes: both go",
     "Напиши им, thanks for waiting или вроде того.",
     'Напиши им, thanks for waiting" или вроде того."',
     "Напиши им, thanks for waiting или вроде того."),
    ("a whole straight pair stays",
     "Напиши им thanks for waiting или вроде того.",
     'Напиши им "thanks for waiting" или вроде того.',
     'Напиши им "thanks for waiting" или вроде того.'),
    ("a pair at the very end stays",
     "Он ответил коротко ок.",
     'Он ответил коротко "ок."',
     'Он ответил коротко "ок."'),
    ("one closing guillemet: goes (as before)",
     "Скажи там, see you soon, и всё.",
     "Скажи там, see you soon», и всё.",
     "Скажи там, see you soon, и всё."),
    ("whole guillemets stay (as before)",
     "Скажи там see you soon и всё.",
     "Скажи там «see you soon» и всё.",
     "Скажи там «see you soon» и всё."),
    ("quotes the speaker had are never touched",
     'Он сказал "ладно" и ушёл.',
     'Он сказал ладно" и ушёл."',
     'Он сказал ладно" и ушёл."'),
]


def main() -> int:
    bad = 0
    for name, raw, locked, want in CASES:
        got = balance_quotes(raw, locked)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}")
        if not ok:
            print("    want:", want)
            print("    got: ", got)
    print(f"\n{len(CASES) - bad} of {len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
