# -*- coding: utf-8 -*-
"""Order versus promise, on invented phrases.

Five phrases where the imperative has to be restored, and five where nothing may
be touched (the speaker is talking about themselves). All taken from real

    ..\\.venv\\Scripts\\python.exe test_person.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt.endings import apply as blind_apply  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.polish import Polisher  # noqa: E402

# (фраза как услышала, что должно получиться в этом месте, почему)
CASES = [
    ("Сделаю session handover.", "Сделай", "поручение в начале фразы"),
    ("Продолжаю.", "Продолжай", "одно слово, но это команда агенту"),
    ("Да, то что оборвалось запускаю.", "запускай", "поручение в конце фразы"),
    ("Ты только сортируй, либо если тебе нужны ключи от этого склада, "
     "то отправляю заявку менеджеру.", "отправляй", "рядом «ты» и «тебе»"),
    ("Как договорились, отгружаю коробки по одной, ставь на каждую метку.",
     "отгружай", "рядом стоит другой приказ — «ставь»"),

    ("Я сначала хочу в магазин сходить. После обеда схожу и посмотрю, "
     "как там полки.", "посмотрю", "он о себе — трогать нельзя"),
    ("И второе, здесь запускаю новую проверку склада, открываю окно с таблицей.",
     "запускаю", "цепочка глаголов о себе"),
    ("Короче, вот добавлю тебе заметки, пока не забыл, по поездке "
     "на дачу.", "добавлю", "«тебе» есть, но говорит о себе"),
    ("Я ярлык в конце приклею, ты делай.", "приклею", "«я» защищает"),
    ("Мне надо место освободить. Запускаю уборку склада здесь не в подвале, "
     "а прямо в гараже.", "Запускаю", "рассказывает, что делает сам"),
]


def main() -> None:
    cfg = cfg_mod.load()
    terms = cfg_mod.glossary()
    fixes = Fixes(cfg_mod.FIXES_PATH)
    pol = Polisher(cfg, terms, fixes, cfg_mod.mywords())
    # короткие фразы тоже отдаём корректору — здесь проверяем именно его
    pol.min_words = 0
    if len(sys.argv) > 1:  # можно задать модель первым доводом
        pol.model = sys.argv[1]
    if not pol.check(force=True):
        print(f"корректор недоступен: {pol.reason}")
        sys.exit(2)
    if len(sys.argv) > 1:
        pol.model = sys.argv[1]
    pol.warmup()
    print(f"корректор: {pol.model}\n")

    from stt.endings import decide

    good_ask = good_llm = good_blind = 0
    import time

    spent = []
    for said, want, why in CASES:
        t0 = time.perf_counter()
        asked, changed = decide(said, pol.ask)
        spent.append(time.perf_counter() - t0)
        out, _t, _note = pol.polish(said)
        blind, _ = blind_apply(said)
        ok_ask, ok_llm, ok_blind = want in asked, want in out, want in blind
        good_ask += ok_ask
        good_llm += ok_llm
        good_blind += ok_blind
        print(f"[вопрос {'v' if ok_ask else 'X'}] "
              f"[корректор {'v' if ok_llm else 'X'}] "
              f"[правило {'v' if ok_blind else 'X'}] {why}")
        if not ok_ask:
            print(f"    было:   {said}")
            print(f"    ждали:  …{want}…")
            print(f"    вышло:  {asked}")
    print(f"\nотдельный вопрос: {good_ask} из {len(CASES)}"
          f"   (в среднем {sum(spent)/len(spent):.2f} с, худшее {max(spent):.2f} с)")
    print(f"корректор целиком: {good_llm} из {len(CASES)}")
    print(f"слепое правило:    {good_blind} из {len(CASES)}")
    sys.exit(0 if good_ask >= 9 else 1)


if __name__ == "__main__":
    main()
