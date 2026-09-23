# -*- coding: utf-8 -*-
"""Что будет, если менять правила про вопросительный знак.

Берёт настоящие надиктовки вместе с ответом правщика (qeval-dump.json) и
разметку «вопрос или нет» (qlabels.json), прогоняет их через нынешние правила
и через предлагаемые, и печатает цену каждого варианта: сколько вопросов
поймано и сколько знаков поставлено зря.

Варианты:
    сейчас        — как работает программа сегодня;
    без «слова-паразита» — вопросительное слово после «Хорошо,», «Окей,»,
                    «Ну,» считается началом фразы, а не серединой;
    + звук        — плюс знак по оценке распознавалки (bench/qscore.py);
    правщику всё  — старое поведение, [polish] questions = "corrector".

    ..\\.venv\\Scripts\\python.exe qpolicy.py
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from stt import config as cfg_mod  # noqa: E402
from stt import polish as P  # noqa: E402
from stt.fixes import Fixes  # noqa: E402

QUESTION = {"question_wording", "question_intonation"}

# Слова, после которых человек начинает мысль заново: сами по себе они ничего
# не значат, но из-за запятой после них вопросительное слово переставало
# считаться началом фразы. Все — из настоящих надиктовок.
FILLERS = {
    "хорошо", "окей", "ок", "ладно", "так", "ну", "вот", "слушай", "смотри",
    "кстати", "короче", "давай", "блядь", "блять", "да", "нет", "и", "а", "но",
    "то", "есть", "значит", "слушайте", "погоди", "подожди", "эй", "алло",
}
WORD = re.compile(r"[^\W_]+", re.UNICODE)


def only_fillers(prefix: str) -> bool:
    """Перед вопросительным словом стоят только слова-паразиты."""
    words = [w.lower() for w in WORD.findall(prefix)]
    return bool(words) and len(words) <= 3 and all(w in FILLERS for w in words)


def patched_may_add(sentence: str) -> bool:
    """Как corrector_may_add, но «Хорошо, как скоро...» — это начало вопроса."""
    ask = P.endings.ASK_RE.search(sentence or "")
    if not ask:
        return False
    prefix = sentence[: ask.start()]
    if ask.group(1).lower() in P.SOFT_ASK and "," in prefix and not only_fillers(prefix):
        return False
    before = len(P.SPLIT_RE.findall(prefix))
    if before >= P.ASK_WITHIN_WORDS:
        return False
    return not P.CLAUSE_BREAK_RE.search(sentence[ask.end():])


def run(dump, allowed, mode="heard", filler=False):
    orig = P.corrector_may_add
    P.questions_mode = mode
    if filler:
        P.corrector_may_add = patched_may_add
    out = {}
    try:
        for r in dump:
            raw, model = r["raw"], r["model"]
            if not model:
                out[r["id"]] = raw
                continue
            out[r["id"]] = P.constrain(raw, model, allowed, set())
    finally:
        P.corrector_may_add = orig
        P.questions_mode = "heard"
    return out


def score(name, texts, labels, dump, acoustic=None, threshold=None):
    caught = false = lost = 0
    for l in labels:
        rid = l["id"]
        if rid not in texts or l["verdict"] == "unclear":
            continue
        got = "?" in texts[rid]
        if not got and acoustic is not None and rid in acoustic:
            if acoustic[rid] > threshold:
                got = True
        is_q = l["verdict"] in QUESTION
        if is_q and got:
            caught += 1
        elif is_q:
            lost += 1
        elif got:
            false += 1
    print(f"{name:<34} поймано {caught:3d} из {caught+lost:3d}"
          f"   потеряно {lost:3d}   лишних {false:3d}")
    return caught, lost, false


def main() -> None:
    cfg = cfg_mod.load()
    terms = cfg_mod.glossary()
    fixes = Fixes(cfg_mod.FIXES_PATH)
    allowed = P.allowed_words(terms, fixes)

    dump = json.loads((HERE / "qeval-dump.json").read_text(encoding="utf-8"))
    labels = json.loads((HERE / "qlabels.json").read_text(encoding="utf-8"))
    if isinstance(labels, dict):
        labels = labels["labels"]
    acoustic = {}
    p = HERE / "qscore.json"
    if p.exists():
        acoustic = {r["id"]: r["d"] for r in json.loads(p.read_text(encoding="utf-8"))}

    logged = {r["id"]: r["final"] for r in dump}
    now = run(dump, allowed, "heard", filler=False)
    same = sum(1 for k in now if now[k] == logged.get(k))
    print(f"проверка пересчёта: совпало с журналом {same} из {len(now)}\n")

    print("=== что даёт каждое правило (3 дня, 421 надиктовка) ===")
    score("в журнале, как было", logged, labels, dump)
    score("сейчас (пересчёт)", now, labels, dump)
    filler = run(dump, allowed, "heard", filler=True)
    score("+ слова-паразиты не мешают", filler, labels, dump)
    for t in (-1.0, -1.5, -2.0, -3.0):
        score(f"+ паразиты + звук (порог {t})", filler, labels, dump, acoustic, t)
    corr = run(dump, allowed, "corrector", filler=False)
    score("правщику всё (старое поведение)", corr, labels, dump)

    print("\n=== что чинят слова-паразиты ===")
    for l in labels:
        rid = l["id"]
        if rid in now and now[rid] != filler.get(rid):
            mark = "вопрос" if l["verdict"] in QUESTION else "НЕ вопрос"
            print(f"  [{mark}] {filler[rid][:100]}")


if __name__ == "__main__":
    main()
