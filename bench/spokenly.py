# -*- coding: utf-8 -*-
"""Разбор истории Spokenly: сколько там речи Антона и что она даёт.

    ..\\.venv\\Scripts\\python.exe spokenly.py stats
    ..\\.venv\\Scripts\\python.exe spokenly.py words [сколько]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HISTORY = Path(r"C:\Users\panto\AppData\Roaming\Spokenly\History")


def records() -> list[dict]:
    out = []
    for jf in sorted(HISTORY.rglob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        ok = (data.get("content", {}).get("dictation", {}) or {}).get("success")
        if not ok:
            continue
        wav = jf.parent / (ok.get("audio_file_name") or "")
        out.append(
            {
                "id": data.get("id"),
                "date": jf.parent.name,
                "created": data.get("creation_date", 0),
                "text": (ok.get("transcription_text") or "").strip(),
                "seconds": float(ok.get("audio_duration") or 0.0),
                "model": ok.get("model_id"),
                "wav": wav if wav.exists() else None,
            }
        )
    return out


def cmd_stats() -> None:
    recs = records()
    total = sum(r["seconds"] for r in recs)
    with_wav = [r for r in recs if r["wav"]]
    print(f"записей: {len(recs)}, из них со звуком: {len(with_wav)}")
    print(f"всего речи: {total/60:.1f} минут ({total/3600:.2f} часа)")
    print(f"средняя длина: {total/max(1,len(recs)):.1f} с")
    by_day: dict[str, list] = {}
    for r in recs:
        by_day.setdefault(r["date"], []).append(r)
    print("\nпо дням:")
    for day in sorted(by_day):
        rs = by_day[day]
        s = sum(x["seconds"] for x in rs)
        print(f"  {day}: {len(rs):>4} шт, {s/60:6.1f} мин")
    models = Counter(r["model"] for r in recs)
    print("\nчем расшифровывал Spokenly:", dict(models))

    buckets = {"<5 с": 0, "5-15 с": 0, "15-30 с": 0, "30-60 с": 0, ">60 с": 0}
    for r in recs:
        s = r["seconds"]
        key = ("<5 с" if s < 5 else "5-15 с" if s < 15 else "15-30 с"
               if s < 30 else "30-60 с" if s < 60 else ">60 с")
        buckets[key] += 1
    print("\nдлина реплик:", buckets)

    words = sum(len(r["text"].split()) for r in recs)
    print(f"слов надиктовано: {words}")


WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{2,}", re.UNICODE)
RU_WORD_RE = re.compile(r"[а-яёА-ЯЁ]{3,}", re.UNICODE)


def cmd_words(limit: int = 80) -> None:
    """Какие английские слова и термины Антон реально произносит."""
    recs = records()
    latin = Counter()
    rus = Counter()
    for r in recs:
        for w in WORD_RE.findall(r["text"]):
            latin[w] += 1
        for w in RU_WORD_RE.findall(r["text"].lower()):
            rus[w] += 1
    print(f"--- латиница в его расшифровках (топ {limit}) ---")
    for w, c in latin.most_common(limit):
        print(f"{c:>4}  {w}")
    print(f"\n--- частые русские слова (топ 40, для понимания словаря) ---")
    for w, c in rus.most_common(40):
        print(f"{c:>4}  {w}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        cmd_stats()
    elif cmd == "words":
        cmd_words(int(sys.argv[2]) if len(sys.argv) > 2 else 80)
