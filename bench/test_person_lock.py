# -*- coding: utf-8 -*-
"""An order must never come back as a promise.

On 2026-08-25 the owner said "Все сам сделай" ("do it all yourself") and the
dictation pasted "Все сам делаю" ("I am doing it all myself") — the opposite
meaning, handed to an agent as an instruction.

The recognizer had heard it RIGHT ("Все сам делай"). Two things broke it:

1. endings.flip_allowed permitted a verb-form swap in EITHER direction, so the
   corrector was free to turn a correct imperative into first person and the
   lock in polish.py waved it through. Measured over 2196 real takes: the
   backwards direction broke 4 phrases and fixed none; the forward one fixed 1.
2. learn.py then counted "делай -> делаю" as an ordinary correction and wrote
   it into fixes.tsv for good, so the blind dictionary would have flipped every
   "делай" from then on, before the corrector even ran.

    ..\\.venv\\Scripts\\python.exe test_person_lock.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.endings import flip_allowed  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.learn import candidate_pairs  # noqa: E402
from stt.polish import allowed_words, constrain  # noqa: E402

TERMS = ["GitHub", "Claude Code", "session handover", "loop", "Codex"]
ALLOWED = allowed_words(TERMS)

# (what was said, what the corrector answered, allowed?, why)
DIRECTION = [
    ("сделаю", "сделай", True, "heard as a promise, meant as an order: allowed"),
    ("проверю", "проверь", True, "the same, another ending"),
    ("делай", "делаю", False, "an order must never become a promise"),
    ("поставь", "поставлю", False, "real case 2026-08-22: 'Сам поставь.'"),
    ("проверь", "проверю", False, "real case 2026-08-24: 'Сейчас проверь.'"),
    ("сделай", "сделаю", False, "the case the owner complained about"),
    ("делай", "делай", False, "same word: nothing to allow"),
    ("делай", "думаю", False, "different verbs: never"),
]

# (what was said, what the corrector answered, what must come out, why)
LOCK = [
    (
        "Все сам делай. Только сначала ещё я хочу больше ресерча.",
        "Все сам делаю. Только сначала ещё я хочу больше ресерча.",
        "Все сам делай. Только сначала ещё я хочу больше ресерча.",
        "2026-08-25, the take the owner marked bad",
    ),
    (
        "Да, всё делай.",
        "Да, всё делаю.",
        "Да, всё делай.",
        "2026-08-22 14:52",
    ),
    (
        "Сам поставь.",
        "Сам поставлю.",
        "Сам поставь.",
        "2026-08-22 21:04",
    ),
    (
        "Сейчас проверь.",
        "Сейчас проверю.",
        "Сейчас проверь.",
        "2026-08-24 09:59",
    ),
    (
        "Сделаю session handover.",
        "Сделай session handover.",
        "Сделай session handover.",
        "the useful direction still works",
    ),
    (
        "Продолжаю.",
        "Продолжай.",
        "Продолжай.",
        "one word, but it is an order to an agent",
    ),
]

# (why, what was said, what the corrector answered, pairs that may be learned)
LEARN = [
    (
        "the broken flip does not get into the dictionary",
        "Все сам делай.",
        "Все сам делаю.",
        [],
    ),
    (
        "and neither does the useful one: the sentence decides, not a list",
        "Сделаю отчёт.",
        "Сделай отчёт.",
        [],
    ),
    (
        "'посмотрю' -> 'посмотри' is the same trap: 'я схожу и посмотрю'",
        "Потом посмотрю.",
        "Потом посмотри.",
        [],
    ),
    (
        "a term heard in Cyrillic is still learned as before",
        "Открой гитхаб.",
        "Открой GitHub.",
        [("гитхаб", "GitHub")],
    ),
    (
        "a neighbouring word must not smuggle the verb past the check",
        "Да, все делай, потом скажи.",
        "Да, всё делаю, потом скажи.",
        [],
    ),
    (
        "and neither must a comma stuck to the word",
        "Да, все делай, потом скажи.",
        "Да, всё делаю потом скажи.",
        [],
    ),
    (
        "a term next to an ordinary word is still learned",
        "Открой мне гитхаб.",
        "Открой мне GitHub.",
        [("гитхаб", "GitHub")],
    ),
]

# (what the dictionary is asked to remember, may it, why)
DICT = [
    ("делай", "делаю", False, "the pair that broke 2026-08-25"),
    ("все делай", "всё делаю", False, "the same, hidden behind a neighbour"),
    ("сделаю", "сделай", False, "the useful direction is a sentence's call too"),
    ("сесть", "сессия", False, "an ordinary Russian verb is not a mangled term"),
    ("гитхаб", "GitHub", True, "a term heard in Cyrillic: this is what the dictionary is for"),
    ("хетцнер", "Hetzner", True, "and another one"),
    ("клод", "Claude", True, "a real word that is also a term: still allowed"),
    ("луп", "loop", True, "and another such"),
]


def main() -> None:
    bad = 0
    total = 0

    print("-- which way a verb form may be flipped")
    for said, other, want, why in DIRECTION:
        total += 1
        got = flip_allowed(said, other)
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {said} -> {other}: {why}")
        if not ok:
            print(f"      wanted {want}, got {got}")

    print("\n-- the lock on the corrector")
    for said, polished, want, why in LOCK:
        total += 1
        got = constrain(said, polished, ALLOWED)
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      said:   {said!r}")
            print(f"      wanted: {want!r}")
            print(f"      got:    {got!r}")

    print("\n-- what self-learning may remember")
    for why, said, polished, want in LEARN:
        total += 1
        got = candidate_pairs(said, polished)
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      said:   {said!r} -> {polished!r}")
            print(f"      wanted: {want}")
            print(f"      got:    {got}")

    print("\n-- what the dictionary itself accepts (the last line of defence)")
    tmp = Path(tempfile.mkdtemp()) / "fixes.tsv"
    for src, dst, want, why in DICT:
        total += 1
        got = Fixes(tmp).add(src, dst, origin="auto")
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {src} -> {dst}: {why}")
        if not ok:
            print(f"      wanted {want}, got {got}")

    print(f"\n{total - bad} of {total} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
