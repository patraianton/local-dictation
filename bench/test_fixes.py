# -*- coding: utf-8 -*-
"""Checks the replacement dictionary: capitals and forbidden pairs.

Every case comes from a real failure on 2026-08-14:
- "Все, закончили." turned into "все, закончили." — the dictionary ate the
  capital letter;

    ..\\.venv\\Scripts\\python.exe test_fixes.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.fixes import Fixes  # noqa: E402


def make(pairs: list[tuple[str, str]]) -> Fixes:
    """A dictionary in a temp file — the real fixes.tsv is never touched."""
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".tsv", delete=False, encoding="utf-8"
    )
    for src, dst in pairs:
        fh.write(f"{src}\t{dst}\t1\tmanual\n")
    fh.close()
    return Fixes(Path(fh.name))


# --- 1. a replacement must not lose the capital letter ---
APPLY_CASES = [
    # (dictionary pairs, what was heard, what must come out, why)
    (
        [("еще", "ещё")],
        "Еще раз, все остальное сделали.",
        "Ещё раз, все остальное сделали.",
        "the capital at the start of a sentence is preserved",
    ),
    (
        [("еще", "ещё")],
        "Давай еще раз.",
        "Давай ещё раз.",
        "mid-sentence the letter stays lowercase",
    ),
    (
        [("хердер", "herdr")],
        "Иди в Хердер и посмотри.",
        "Иди в herdr и посмотри.",
        "a name keeps its own spelling: herdr stays lowercase even if heard capitalized",
    ),
    (
        [("айфон", "iPhone")],
        "Айфон дай сюда.",
        "iPhone дай сюда.",
        "a term with its own capitals is left as written (iPhone, not IPhone)",
    ),
    (
        [("work tree", "worktree")],
        "Work tree открой.",
        "worktree открой.",
        "a Latin-script name gets no capital here: the corrector adds it",
    ),
    (
        [("щас", "сейчас")],
        "Щас сделаю.",
        "Сейчас сделаю.",
        "an ordinary word does get a capital at the start of a sentence",
    ),
    (
        [("хендовер", "handover")],
        "Сделай хендовер.",
        "Сделай handover.",
        "a lowercase letter stays lowercase",
    ),
]

# --- 2. which pairs the dictionary must REFUSE ---
# (existing pairs, pair to add, origin, returns True?, ends up stored?, why)
ADD_CASES = [
    (
        [("все", "всё")],
        ("всё", "все"),
        "auto",
        False,
        False,
        "a reverse pair: the two would fight each other",
    ),
    (
        [],
        ("все", "всё"),
        "auto",
        False,
        False,
        "the machine does not invent e/yo pairs: they are different words",
    ),
    (
        [],
        ("еще", "ещё"),
        "manual",
        True,
        True,
        "a human may add an e/yo pair by hand: they can see the meaning",
    ),
    (
        [],
        ("хендовер", "handover"),
        "auto",
        True,
        True,
        "an ordinary replacement is still learned automatically",
    ),
    (
        [("луп", "loop")],
        ("луп", "loop"),
        "auto",
        False,
        True,
        "a known pair is not counted as new, but is not thrown away either",
    ),
]


def main() -> None:
    bad = 0

    for pairs, said, want, why in APPLY_CASES:
        fx = make(pairs)
        got, _n = fx.apply(said)
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      said:   {said}")
            print(f"      wanted:  {want}")
            print(f"      got:     {got}")

    for pairs, (src, dst), origin, want_added, want_in_dict, why in ADD_CASES:
        fx = make(pairs)
        got_added = fx.add(src, dst, origin=origin)
        in_dict = fx.pairs.get(src.lower(), ("", 0, ""))[0] == dst
        ok = got_added == want_added and in_dict == want_in_dict
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      adding: {src} -> {dst} ({origin})")
            print(f"      wanted: new={want_added}, stored={want_in_dict}")
            print(f"      got:    new={got_added}, stored={in_dict}")

    total = len(APPLY_CASES) + len(ADD_CASES)
    print(f"\n{total-bad} of {total} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
