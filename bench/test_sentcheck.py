# -*- coding: utf-8 -*-
"""Checks stt.sentcheck: finding a take in the message that was really sent.

Invented phrases and a fake transcript folder; nothing real is read.

    ..\\.venv\\Scripts\\python.exe test_sentcheck.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import sentcheck  # noqa: E402

T0 = datetime(2026, 3, 2, 10, 0, 0).astimezone()


def take_id(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%d_%H%M%S") + "-000"


def stamp(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def user(minutes: float, text: str) -> dict:
    return {"type": "user", "timestamp": stamp(minutes), "message": {"role": "user", "content": text}}


RECORDS = [
    # sent exactly as pasted
    user(0.2, "Проверь, пожалуйста, вчерашний отчёт по складу."),
    # pasted, then one word fixed by hand, with typed text after it
    user(5.3, "Открой таблицу продаж и найди пустые строки. ещё вот это глянь"),
    # a message sent mid-turn: stored as a queued command
    {"type": "attachment", "timestamp": stamp(10.1), "attachment": {
        "type": "queued_command", "prompt": "Сколько у нас осталось коробок на полке?",
        "origin": {"kind": "human"}}},
    # the harness repeating the take back: must not count as sent
    user(15.1, "<task-notification>\nTAKE Запусти тесты ещё раз ночью.</task-notification>"),
    # an answer typed into a question with options
    {"type": "user", "timestamp": stamp(20.2), "message": {"role": "user", "content": [
        {"type": "tool_result", "content":
            'The user answered: "Какой вариант?"="Давай второй, он короче и проще". '
            'Read the answers carefully.'}]}},
    # a take dropped and said again in other words
    user(25.5, "Короче, сделай так, чтобы окно само закрывалось через минуту."),
    # two takes pasted into one message
    user(31.0, "Первое: позвони на склад. Второе: напиши поставщику про сроки."),
    # a typed message sharing one word with a two-word take
    user(50.2, "Каких цветов? допустим"),
    # one long message built from three takes in a row
    user(56.0, "Собери отчёт по складу с утра и проверь остатки. Отправь его "
               "поставщику с пометкой о сроках поставки. С низкими остатками разберёмся завтра."),
    # a typed message sharing three scattered common words with a take
    user(61.0, "именно он придёт. Ничего соседям не говори"),
    # a take with its last word deleted and a picture pasted in its place
    user(66.0, "Закрой окно в спальне, [Image #1]"),
    # a later typed message that shares only two tiny words with a short take
    user(71.0, "сегодня я даже не собираюсь туда ехать"),
    # another window writing on the owner's behalf, quoting part of a take
    user(76.0, "проверь склад после сбоя; владелец просит: «нужны свежие остатки, "
               "цена потом» — срочно"),
    # the same, the quote holding the whole take, with other words around it
    user(81.0, "итог по ночи. Вопрос владельца: «почему отчёт пришёл пустой?» Разберись"),
    # the owner's own message with a quoted word and a real hand edit
    user(86.0, "Напиши поставщику «спасибо» и закрой заказ"),
    # a take broken off and said again whole at once: one message, the second take
    user(91.2, "Расскажи мне, пожалуйста, про тот отчёт по складу, где он?"),
    # a typed letter that shares only everyday words with a short take
    user(95.0, "Добрый день. Спасибо за письмо. На прошлой неделе у меня прошёл переезд "
               "склада, поэтому отвечаю только сейчас. Цены согласуем на встрече."),
    # a short take with a word added by hand, in a long message built from takes
    user(101.0, "Отчёт собран, остатки проверены. С более низкими ценами разберёмся "
                "отдельно. Поставщику напишу вечером."),
    # a take broken off and said again in a long take that ended minutes later
    user(107.8, "Напиши ответ поставщику так, чтобы он не обиделся, как мне это сказать "
                "помягче, и добавь про сроки."),
    # a summary of a compacted conversation quoting the owner: not a sent message
    {**user(45.5, "This session is being continued from a previous conversation. "
                  "Owner said: «Полей цветы на балконе и закрой окно»."), "isCompactSummary": True,
     "isVisibleInTranscriptOnly": True},
]

TAKES = [
    (0, "Проверь, пожалуйста, вчерашний отчёт по складу.", "same", []),
    (5, "Открой таблицу продаж и найди пустые строчки.", "edited", [("строчки", "строки")]),
    (10, "Сколько у нас осталось коробок на полке?", "same", []),
    (15, "Запусти тесты ещё раз ночью.", "not_found", []),
    (26, "Надо, чтобы вот это окошко пропадало.", "not_found", []),
    (20, "Давай второй, он короче и проще.", "same", []),
    (25, "Слушай, сделай так, чтобы окошко закрывалось.", "redictated", []),
    (30.5, "Первое: позвони на склад.", "same", []),
    (30.7, "Второе: напиши поставщику про срок.", "edited", [("срок", "сроки")]),
    (40, "Ё-моё, всё ещё не готово?", "not_found", []),
    (45, "Полей цветы на балконе и закрой окно.", "not_found", []),
    (50, "Ну, допустим.", "not_found", []),
    (55.5, "Отправь его поставщику с пометкой о сроках поставки.", "same", []),
    (55.7, "С низкими остатками разберёмся завтра.", "same", []),
    (60.5, "А он больше ничего не спрашивал?", "not_found", []),
    (65.5, "Закрой окно в спальне, пожалуйста.", "edited", [("пожалуйста", "")]),
    (70.5, "Я не вижу.", "not_found", []),
    (75.5, "Я заказал большую партию, нужны свежие остатки, цена потом. Значит...", "quoted", []),
    (80.5, "Почему отчёт пришёл пустой и без даты?", "quoted", []),
    (85.5, "Напиши поставщику спасибо и закрой счёт.", "edited", []),
    (90.5, "Расскажи, пожалуйста, про тот...", "superseded", []),
    (90.8, "Расскажи мне, пожалуйста, про тот отчёт по складу, где он?", "same", []),
    (94.5, "У меня есть", "not_found", []),
    (100.5, "С низкими ценами.", "edited", []),
    (105.0, "Напиши ответ поставщику. Как мне...", "superseded", []),
    (107.6, "Напиши ответ поставщику так, чтобы он не обиделся, как мне это сказать "
            "помягче, и добавь про сроки.", "same", []),
]
# Length of a take, where it matters: a take is stamped when it ends.
SECONDS = {107.6: 95.0}


def main() -> int:
    root = Path(tempfile.mkdtemp()) / "projects"
    (root / "proj").mkdir(parents=True)
    with open(root / "proj" / "session.jsonl", "w", encoding="utf-8") as fh:
        for rec in RECORDS:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    inbox = sentcheck.Inbox([root])
    inbox.poll(T0 - timedelta(days=1))
    bad = 0
    every = [{"id": take_id(m), "final": t, "raw": t, "seconds_audio": SECONDS.get(m, 3.0)}
             for m, t, _w, _f in TAKES]
    for minutes, text, want, want_fixes in TAKES:
        later = [t for t in every if t["id"] > take_id(minutes)]
        res = sentcheck.check({"id": take_id(minutes), "final": text, "raw": text}, inbox, later=later)
        got_fixes = [(h["pasted"], h["sent"]) for h in res.get("hunks", [])]
        ok = res["status"] == want and (not want_fixes or got_fixes == want_fixes)
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {want:10s} {text}")
        if not ok:
            print(f"      got {res['status']} {got_fixes} score={res['score']}")
    print(f"\n{len(TAKES) - bad} of {len(TAKES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
