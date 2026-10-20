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
    (
        [("royce, net", "рой Sonnet")],
        "Для простых задач Royce, Net хватит.",
        "Для простых задач рой Sonnet хватит.",
        "a Latin word heard mid-sentence for a Russian one gets no capital",
    ),
    (
        [("roy", "рой")],
        "Готово. Roy агентов запускай.",
        "Готово. Рой агентов запускай.",
        "the same Latin word after a full stop does get one",
    ),
    (
        [("t-оркестратор", "ты оркестратор")],
        "T-оркестратор, проверь отчёт.",
        "Ты оркестратор, проверь отчёт.",
        "and at the very start of the take",
    ),
    (
        [("щас", "сейчас")],
        "Мы всё обсудили, Потом щас решим.",
        "Мы всё обсудили, потом сейчас решим.",
        "an ordinary word after a comma loses the capital a long take gave it",
    ),
    (
        [("щас", "сейчас")],
        "Мы позвонили, И щас решим, В общем.",
        "Мы позвонили, и сейчас решим, в общем.",
        "a one-letter conjunction or preposition too (the dictionary also lists them as abbreviations)",
    ),
    (
        [("щас", "сейчас")],
        "Я позвал, Марина щас придёт, Москва ждёт.",
        "Я позвал, Марина сейчас придёт, Москва ждёт.",
        "a name or a city after a comma keeps its capital",
    ),
    (
        [("щас", "сейчас")],
        "Нашли щас какие-то, Какие-то ошибки, Где-нибудь в Санкт-Петербурге, Нью-Йорк.",
        "Нашли сейчас какие-то, какие-то ошибки, где-нибудь в Санкт-Петербурге, Нью-Йорк.",
        "a word with -то or -нибудь after a comma loses the capital; a hyphenated name keeps it",
    ),
    (
        [("щас", "сейчас")],
        "Открой щас, Hermes и Зорбикс.",
        "Открой сейчас, Hermes и Зорбикс.",
        "a Latin term or a word the dictionary does not know is left alone",
    ),
    (
        [("щас", "сейчас")],
        "Щас есть А – таблица, Б – журнал, В: архив, Г) почта, Д - заметки.",
        "Сейчас есть А – таблица, Б – журнал, В: архив, Г) почта, Д - заметки.",
        "a letter that labels an item of a list keeps its capital",
    ),
    (
        [("щас", "сейчас")],
        "По складу вариант А берём щас, Б уточню завтра.",
        "По складу вариант А берём сейчас, Б уточню завтра.",
        "a list letter with no dash after it keeps its capital too",
    ),
    (
        [("ройсона от агентов", "рой Sonnet-агентов")],
        "Отправь туда Ройсона от агентов. Ройсона от агентов тоже.",
        "Отправь туда рой Sonnet-агентов. Рой Sonnet-агентов тоже.",
        "a Russian word heard as a name mid-sentence: the capital comes from the position",
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

    # --- 3. the file is edited by hand while the dictionary is running ---
    fx = make([("еще", "ещё")])
    with open(fx.path, "a", encoding="utf-8") as fh:
        fh.write("кубернетис\tKubernetes\t0\tmanual\n")
    got, _n = fx.apply("Подними кубернетис еще раз.")
    ok = got == "Подними Kubernetes ещё раз."
    bad += 0 if ok else 1
    print(f"[{'v' if ok else 'X'}] a pair written into the file by hand works on the next phrase")
    if not ok:
        print(f"      got:     {got}")

    fx = make([("еще", "ещё")])
    with open(fx.path, "a", encoding="utf-8") as fh:
        fh.write("графана\tGrafana\t0\tmanual\n")
    fx.add("постгрес", "Postgres", origin="auto")
    on_disk = fx.path.read_text(encoding="utf-8")
    ok = "графана\tGrafana" in on_disk and "постгрес\tPostgres" in on_disk
    bad += 0 if ok else 1
    print(f"[{'v' if ok else 'X'}] a learned pair does not erase a pair written by hand")
    if not ok:
        print(f"      file:    {on_disk!r}")

    total = len(APPLY_CASES) + len(ADD_CASES) + 2
    print(f"\n{total-bad} of {total} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
