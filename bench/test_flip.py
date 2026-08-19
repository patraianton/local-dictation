# -*- coding: utf-8 -*-
"""Проверка клавиши «это был вопрос»: точка <-> вопрос в конце диктовки.

    ..\\.venv\\Scripts\\python.exe test_flip.py

Зачем клавиша. Вопрос от утверждения в его речи отличается только голосом, а по
голосу он не ловится: замер 14.08.2026 на 347 записях поймал 2 вопроса из 117.
Значит, последнее слово за человеком — но одной кнопкой.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import flip_question  # noqa: E402

# (что вставлено, что должно стать, сколько стереть, что набрать, зачем)
CASES = [
    (
        "Скоро это уже закончится.",
        "Скоро это уже закончится?",
        1, "?",
        "точка меняется на вопрос — главный случай",
    ),
    (
        "Переводи их на ажур?",
        "Переводи их на ажур.",
        1, ".",
        "и обратно: лишний вопрос снимается той же кнопкой",
    ),
    (
        "И вот опять...",
        "И вот опять?",
        3, "?",
        "три точки стираем целиком, иначе останется «И вот опять..?»",
    ),
    (
        "И вот опять…",
        "И вот опять?",
        1, "?",
        "многоточие одним знаком — тоже один стёртый знак",
    ),
    (
        "Сделай отчёт!",
        "Сделай отчёт?",
        1, "?",
        "восклицательный знак тоже меняется",
    ),
    (
        "Убери loop",
        "Убери loop?",
        0, "?",
        "знака не было — ничего не стираем, просто дописываем",
    ),
    (
        "Первое предложение. И второе.",
        "Первое предложение. И второе?",
        1, "?",
        "меняем только конец, точка в середине не трогается",
    ),
    (
        "",
        "",
        0, "",
        "пустая диктовка ничего не ломает",
    ),
    (
        "Что странно.   ",
        "Что странно?   ",
        1, "?",
        "пробелы в хвосте остаются на месте",
    ),
]


def main() -> None:
    bad = 0
    for said, want_text, want_erase, want_type, why in CASES:
        got_text, got_erase, got_type = flip_question(said)
        ok = (got_text, got_erase, got_type) == (want_text, want_erase, want_type)
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      было:   {said!r}")
            print(f"      ждали:  {want_text!r}, стереть {want_erase}, "
                  f"набрать {want_type!r}")
            print(f"      вышло:  {got_text!r}, стереть {got_erase}, "
                  f"набрать {got_type!r}")

    # Дважды нажал — вернулось как было. Спрашиваем это только с обычных «.» и
    # «?»: из «И вот опять...» многоточие не восстановить, там знак один.
    roundtrip_bad = 0
    for said, *_rest in CASES:
        tail = said.rstrip()
        if not tail.endswith((".", "?")) or tail.endswith("..."):
            continue
        once, _e, _t = flip_question(said)
        twice, _e, _t = flip_question(once)
        if twice != said:
            roundtrip_bad += 1
            print(f"[X] два нажатия должны вернуть как было: {said!r} -> {twice!r}")
    bad += roundtrip_bad
    if not roundtrip_bad:
        print("[v] два нажатия возвращают текст как был")

    print(f"\n{len(CASES)+1-bad} из {len(CASES)+1} сошлось")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
