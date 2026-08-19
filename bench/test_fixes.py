# -*- coding: utf-8 -*-
"""Проверка словаря замен: заглавные буквы и запрет вредных пар.

    ..\\.venv\\Scripts\\python.exe test_fixes.py

Все случаи — из настоящих сбоев 14.08.2026:
- «Все, закончили.» превратилось в «все, закончили.» — словарь съел заглавную;
- в словарь сами добавились встречные пары «все -> всё» И «всё -> все»,
  они воевали друг с другом и испортили текст в 7 диктовках.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.fixes import Fixes  # noqa: E402


def make(pairs: list[tuple[str, str]]) -> Fixes:
    """Словарь во временном файле — настоящий fixes.tsv не трогаем."""
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".tsv", delete=False, encoding="utf-8"
    )
    for src, dst in pairs:
        fh.write(f"{src}\t{dst}\t1\tmanual\n")
    fh.close()
    return Fixes(Path(fh.name))


# --- 1. замена не должна терять заглавную букву ---
APPLY_CASES = [
    # (пары словаря, что услышала, что должно получиться, зачем проверяем)
    (
        [("еще", "ещё")],
        "Еще раз, все остальное сделали.",
        "Ещё раз, все остальное сделали.",
        "заглавная в начале предложения сохраняется",
    ),
    (
        [("еще", "ещё")],
        "Давай еще раз.",
        "Давай ещё раз.",
        "в середине предложения буква остаётся маленькой",
    ),
    (
        [("хердер", "herdr")],
        "Иди в Хердер и посмотри.",
        "Иди в herdr и посмотри.",
        "у названия написание своё: herdr маленькими, даже если услышала с большой",
    ),
    (
        [("айфон", "iPhone")],
        "Айфон дай сюда.",
        "iPhone дай сюда.",
        "у термина со своими заглавными написание не трогаем (iPhone, не IPhone)",
    ),
    (
        [("work tree", "worktree")],
        "Work tree открой.",
        "worktree открой.",
        "название латиницей заглавную не получает — её поставит корректор",
    ),
    (
        [("щас", "сейчас")],
        "Щас сделаю.",
        "Сейчас сделаю.",
        "русское слово заглавную в начале предложения получает",
    ),
    (
        [("хендовер", "handover")],
        "Сделай хендовер.",
        "Сделай handover.",
        "маленькая буква остаётся маленькой",
    ),
]

# --- 2. какие пары словарь принимать НЕ должен ---
# (что уже есть, что добавляем, откуда, вернёт ли True, окажется ли в словаре, зачем)
ADD_CASES = [
    (
        [("все", "всё")],
        ("всё", "все"),
        "auto",
        False,
        False,
        "встречная пара: «все->всё» и «всё->все» воюют друг с другом",
    ),
    (
        [],
        ("все", "всё"),
        "auto",
        False,
        False,
        "сам себе «е/ё» не придумывает: все и всё — разные слова",
    ),
    (
        [],
        ("еще", "ещё"),
        "manual",
        True,
        True,
        "руками «е/ё» добавить можно — человек видит смысл",
    ),
    (
        [],
        ("хендовер", "handover"),
        "auto",
        True,
        True,
        "обычную замену по-прежнему учит сам",
    ),
    (
        [("луп", "loop")],
        ("луп", "loop"),
        "auto",
        False,
        True,
        "уже известную пару новой не считает, но из словаря не выкидывает",
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
            print(f"      сказал: {said}")
            print(f"      ждали:  {want}")
            print(f"      вышло:  {got}")

    for pairs, (src, dst), origin, want_added, want_in_dict, why in ADD_CASES:
        fx = make(pairs)
        got_added = fx.add(src, dst, origin=origin)
        in_dict = fx.pairs.get(src.lower(), ("", 0, ""))[0] == dst
        ok = got_added == want_added and in_dict == want_in_dict
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      добавляли: {src} -> {dst} ({origin})")
            print(f"      ждали: новая={want_added}, в словаре={want_in_dict}")
            print(f"      вышло: новая={got_added}, в словаре={in_dict}")

    total = len(APPLY_CASES) + len(ADD_CASES)
    print(f"\n{total-bad} из {total} сошлось")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
