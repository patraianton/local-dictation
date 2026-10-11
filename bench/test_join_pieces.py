# -*- coding: utf-8 -*-
"""Checks stt.asr.join_pieces: the pieces of a long take glued back together.

A piece that starts mid-sentence loses the capital the recognizer gave it;
a new sentence, a name and an unknown word keep theirs. Invented phrases.

    ..\\.venv\\Scripts\\python.exe test_join_pieces.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import join_pieces  # noqa: E402

CASES = [
    # mid-sentence: the capital goes
    (["Склад закрыли раньше, ведь в тот день", "Это был последний заказ."],
     "Склад закрыли раньше, ведь в тот день это был последний заказ."),
    (["Отправь всех помощников", "Сделать новую таблицу цен."],
     "Отправь всех помощников сделать новую таблицу цен."),
    (["Мы проверили ту коробку,", "Которую привезли вчера."],
     "Мы проверили ту коробку, которую привезли вчера."),
    # a new sentence keeps its capital
    (["Склад закрыли.", "Это был последний заказ."],
     "Склад закрыли. Это был последний заказ."),
    (["Ты это видел?", "Сделай копию."], "Ты это видел? Сделай копию."),
    (["Ну и вот…", "Потом разберёмся."], "Ну и вот… Потом разберёмся."),
    # names, places and unknown words keep theirs
    (["Отчёт пришлёт", "Марина завтра утром."], "Отчёт пришлёт Марина завтра утром."),
    (["Поедем через", "Москву, наверное."], "Поедем через Москву, наверное."),
    (["Это надо залить в", "Гитлаб сегодня."], "Это надо залить в Гитлаб сегодня."),
    # Latin words are not touched, nor a hyphenated first word
    (["Посмотри в", "Notion, там всё есть."], "Посмотри в Notion, там всё есть."),
    (["Он был", "Северо-западный ветер."], "Он был Северо-западный ветер."),
    # one piece, empty pieces
    (["Это была одна фраза."], "Это была одна фраза."),
    (["", "Это вторая", " "], "Это вторая"),
    ([], ""),
]


def main() -> int:
    bad = 0
    for pieces, want in CASES:
        got = join_pieces(pieces)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {want}")
        if not ok:
            print(f"      got {got}")
    print(f"\n{len(CASES) - bad} of {len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
