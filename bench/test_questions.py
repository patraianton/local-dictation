# -*- coding: utf-8 -*-
"""Вернёт ли корректор вопросительный знак.

Первые 7 — настоящие потерянные вопросы с записей Антона.
Остальные — утверждения, на которые «?» ставить нельзя.

    ..\\.venv\\Scripts\\python.exe test_questions.py [модель]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.polish import Polisher  # noqa: E402

QUESTIONS = [
    "Я имею в виду, что конкретно будет видеться.",
    "Мне в этой картинке непонятны итерации между ранами, что менялось.",
    "Просто хуй на него забьём.",
    "Мы заливаем статьи уже.",
    "По статистике, в смысле.",
    "Как это, блядь, у нас два клика за неделю и слабое место — это три ручные машины.",
    "Дай какую-то сводку по этому, что она хочет.",
]
STATEMENTS = [
    "Восстанови все прогоны и те, которые пропустили, тоже.",
    "Мне надо токены пожечь, запускай запись контента локально на Opus агентах.",
    "Я карту в конце привяжу, ты делай.",
    "Все верифицировано, все перепроверено.",
    "Это отдельный большой проект, его надо хорошенько сделать.",
    "Сохрани себе от него в env ключ.",
    "Там дохуя жмут на кнопку, но никто форму не заполняет.",
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
