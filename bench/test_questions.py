# -*- coding: utf-8 -*-
"""Will the corrector restore a question mark.

The first 7 are invented phrases of the same shape as real lost questions.
The rest are statements that must not get a "?".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.polish import Polisher  # noqa: E402

QUESTIONS = [
    "Я спрашиваю, что конкретно будет лежать на полке.",
    "Мне в этой таблице непонятны отличия между неделями, что менялось.",
    "Просто забьём на него.",
    "Мы красим забор уже.",
    "По накладной, в смысле.",
    "Как это у нас две поставки за неделю и узкое место — это три старые тележки.",
    "Дай какой-то отчёт по этому, что он показывает.",
]
STATEMENTS = [
    "Верни все посылки и те, которые потеряли, тоже.",
    "Мне надо токены пожечь, запускай запись контента локально на Opus агентах.",
    "Я ярлык в конце приклею, ты делай.",
    "Всё упаковано, всё подписано.",
    "Это отдельный большой проект, его надо хорошенько сделать.",
    "Сохрани себе от него в заметки адрес.",
    "Там многие заходят на сайт, но никто анкету не заполняет.",
]


def main() -> None:
    cfg = cfg_mod.load()
    pol = Polisher(cfg, cfg_mod.glossary(), Fixes(cfg_mod.FIXES_PATH), cfg_mod.mywords())
    pol.min_words = 0
    if not pol.check(force=True):
        print(f"корректор недоступен: {pol.reason}")
        sys.exit(2)
    if len(sys.argv) > 1:
        pol.model = sys.argv[1]
    pol.warmup()
    print(f"корректор: {pol.model}\n")

    # Два случая, потому что с 15.08.2026 знак от корректора принимается, только
    # если распознавалка сама услышала вопрос ИЛИ в тексте есть вопросительное
    # слово. Тут все реплики записаны с точкой, так что первый столбец — это
    # «распознавалка вопроса не услышала», худший случай.
    def heard(text: str) -> str:
        """Как если бы распознавалка сама поставила знак."""
        return text.rstrip().rstrip(".") + "?"

    alone = with_asr = 0
    print("вопрос                                   сам   если услышала")
    for text in QUESTIONS:
        a = pol.polish(text)[0].rstrip().endswith("?")
        b = pol.polish(heard(text))[0].rstrip().endswith("?")
        alone += a
        with_asr += b
        print(f"[{'v' if a else 'X'}][{'v' if b else 'X'}] {text[:70]}")

    print()
    false = 0
    for text in STATEMENTS:
        out, _t, _n = pol.polish(text)
        bad = out.rstrip().endswith("?")
        false += bad
        print(f"[{'X' if bad else 'v'}] утверждение: {out[:88]}")

    print(f"\nпоймано, когда распознавалка знак НЕ слышала: {alone} из {len(QUESTIONS)}")
    print(f"поймано, когда услышала:                     {with_asr} из {len(QUESTIONS)}")
    print(f"испорчено утверждений:                       {false} из {len(STATEMENTS)}")
    print("\nНа настоящих записях распознавалка слышит знак сама в 33 вопросах из 43 "
          "(замер 15.08.2026), поэтому правый столбец ближе к жизни.")
    sys.exit(0 if with_asr >= 5 and false == 0 else 1)


if __name__ == "__main__":
    main()
