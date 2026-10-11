# -*- coding: utf-8 -*-
"""Checks verb-ending repair: an order must stay an order.

Cases 1-6 are real, taken from divergences with ElevenLabs on real recordings.
The rest are traps where the rule must not fire.

    ..\\.venv\\Scripts\\python.exe test_endings.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.endings import apply  # noqa: E402

CASES = [
    # --- real failures from actual recordings ---
    ("Сделаю session handover", "Сделай session handover", "an order at the start of the phrase"),
    ("Продолжаю.", "Продолжай.", "a single word, and it is an order"),
    ("Да, то, что осталось, запускаю",
     "Да, то, что осталось, запускай", "an order at the end of the phrase"),
    ("то отправляю backup в архив", "то отправляй backup в архив", "an order in the middle"),
    ("по общему графику отгружаю одну за другой",
     "по общему графику отгружай одну за другой", "imperative, not first person"),
    ("Сформирую отчёт по неделе", "Сформируй отчёт по неделе", "the canonical example"),

    # --- traps: must not be touched ---
    ("Я сделаю это сам", "Я сделаю это сам", "with the pronoun present, it really is about the speaker"),
    ("Ладно, я посмотрю завтра", "Ладно, я посмотрю завтра", "the pronoun at the start of the phrase"),
    ("Мы соберу... мы соберём это", "Мы соберу... мы соберём это", "with the plural pronoun it is left alone too"),
    # ЦЕНА ПРАВИЛА, осознанная: «Проверяю всё сам» — правда о себе, но без «я»
    # в этом же предложении отличить его от приказа нельзя («сделай сам» —
    # тоже приказ). Выбор в пользу приказов: он диктует команды, а не отчёты.
    # Каждая такая правка попадает в журнал и видна на странице.
    ("Я сделаю. Проверяю всё сам.", "Я сделаю. Проверяй всё сам.",
     "no pronoun in its own sentence: treated as an order (the price of the rule)"),
    ("А почему первую коробку не отправили?",
     "А почему первую коробку не отправили?", "nouns with that ending are left alone"),
    ("предложи мне email-кампанию пробную",
     "предложи мне email-кампанию пробную", "adjectives with that ending are left alone"),
    ("Не могу попасть в эту станцию", "Не могу попасть в эту станцию",
     "not a verb at all"),
    ("Дай справку по складам", "Дай справку по складам", "also not a verb"),

    # --- the second sentence is judged from scratch ---
    ("Я закончил. Сделаю handover.", "Я закончил. Сделай handover.",
     "a pronoun in the previous sentence does not protect the next one"),
]


def main() -> None:
    bad = 0
    for said, want, why in CASES:
        got, changed = apply(said)
        ok = got == want
        if not ok:
            bad += 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      was:     {said}")
            print(f"      wanted:  {want}")
            print(f"      got:     {got}")
    print(f"\n{len(CASES)-bad} of {len(CASES)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
