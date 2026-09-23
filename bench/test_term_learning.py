# -*- coding: utf-8 -*-
"""Названия должны выучиваться, а не копиться вечными одиночками.

31.08.2026. Антон: «коверкает названия». В state/candidates.json в тот день
лежало ровно доказательство: «автопассе», «аутопаса», «автопаз», «автопасса» —
четыре написания одного autopase, у каждого по одному срабатыванию. Правило
«запомнить после двух повторов» считало повторы по паре целиком, а имя
коверкается каждый раз по-новому — значит счётчик не доходил до двух НИКОГДА,
и именно те слова, которые больнее всего, не выучивались вовсе. То же с
Vercel («павершели», «версель», «версале», «верселя») и Herdr («хердере»,
«хёрдере», «хордер»).

Теперь у слова из словаря счёт ведётся по самому слову: правщик дважды
исправил что-нибудь на autopase — третий кривой вариант запоминается сразу.

Вторая половина проверки — про подсказку распознавалке: 45 её мест
раздаются по тому, как часто слово звучит, а не по порядку строк в файле.

    ..\\.venv\\Scripts\\python.exe test_term_learning.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stt import termstats  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.learn import Learner  # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'[v]' if ok else '[X]'} {name}: получилось {got}, ожидалось {want}")
    if not ok:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="stt-terms-"))
CFG = {"learn": {"enabled": True, "promote_after": 2, "keep_audio": False}}
TERMS = ["autopase", "Vercel", "herdr", "Opus"]


_n = [0]


def fresh():
    """Каждый раз чистый словарь и чистая копилка кандидатов."""
    _n[0] += 1
    fixes = Fixes(tmp / f"fixes{_n[0]}.tsv")
    learner = Learner(CFG, fixes, tmp, tmp, tmp / f"cand{_n[0]}.json", TERMS)
    return fixes, learner


print("--- имя, которое коверкается каждый раз по-новому ---")
fixes, learner = fresh()
learner.observe("зайди в автопассе", "зайди в autopase")
check("первое искажение: ещё рано", "автопассе" in fixes.pairs, False)
learner.observe("смотри аутопаса", "смотри autopase")
check("второе, другое искажение: слово выучено", "аутопаса" in fixes.pairs, True)
learner.observe("а в автопаз что", "а в autopase что")
check("третье искажение запоминается сразу", "автопаз" in fixes.pairs, True)

print()
print("--- обычное слово так не выучивается: нужны два одинаковых повтора ---")
fixes, learner = fresh()
learner.observe("щас посмотрю", "сейчас посмотрю")
learner.observe("щаз гляну", "сейчас гляну")
check("два разных искажения обычного слова не складываются", len(fixes.pairs), 0)
learner.observe("щас посмотрю", "сейчас посмотрю")
check("а два одинаковых — складываются", "щас" in fixes.pairs, True)

print()
print("--- старые одиночки не пропадают при переходе на новый формат ---")
old = tmp / "old.json"
old.write_text(json.dumps({
    "автопассе\tautopase": 1, "аутопаса\tautopase": 1,
}, ensure_ascii=False), encoding="utf-8")
fixes2 = Fixes(tmp / "fixes2.tsv")
learner2 = Learner(CFG, fixes2, tmp, tmp, old, TERMS)
check("две прежние одиночки засчитаны как два раза за autopase",
      learner2.by_target.get("autopase"), 2)
learner2.observe("открой автопасса", "открой autopase")
check("поэтому следующее искажение выучивается сразу",
      "автопасса" in fixes2.pairs, True)

print()
print("--- подсказка распознавалке: по частоте, а не по порядку строк ---")
logs = tmp / "logs"
logs.mkdir(exist_ok=True)
lines = []
for _ in range(30):
    lines.append({"final": "залей autopase на сервер", "raw": "залей автопас на сервер"})
for _ in range(3):
    lines.append({"final": "проверь Bitrix", "raw": "проверь Bitrix"})
(logs / "2026-08-30.jsonl").write_text(
    "\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")

terms = ["Максим", "Даниил", "Алина", "Ноам", "Bitrix", "autopase"]
order = termstats.rank(terms, logs, aliases={}, days=14)
check("часто звучащее слово поднялось выше редкого",
      order.index("autopase") < order.index("Bitrix"), True)
check("имена людей остаются закреплёнными сверху", order[:4], terms[:4])

# Слово, которое распознавалка ВСЕГДА слышит неправильно, в текстах под своим
# именем не встречается вовсе — его считают по кривым вариантам из fixes.tsv.
lines = [{"final": "открой хермес", "raw": "открой хермес"} for _ in range(20)]
lines += [{"final": "проверь Bitrix", "raw": "проверь Bitrix"} for _ in range(3)]
(logs / "2026-08-31.jsonl").write_text(
    "\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
terms = ["Максим", "Даниил", "Алина", "Ноам", "Bitrix", "Hermes"]
order = termstats.rank(terms, logs, aliases={"hermes": {"хермес"}}, days=14)
check("слово, которое всегда слышат неправильно, всё равно попадает наверх",
      order.index("Hermes") < order.index("Bitrix"), True)

print()
print("НЕ СОШЛОСЬ: " + ", ".join(FAIL) if FAIL else "всё сошлось")
sys.exit(1 if FAIL else 0)
