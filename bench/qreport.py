# -*- coding: utf-8 -*-
"""Сколько вопросов теряется и что помогло бы их вернуть.

Складывает три вещи:
    qeval-dump.json  — что услышала распознавалка, что ответил правщик, что
                       получил человек (bench/qeval.py);
    qscore.json      — насколько звук похож на вопрос (bench/qscore.py);
    qlabels.json     — разметка «вопрос или нет», сделанная отдельно.

и печатает: сколько вопросов дошло до человека сейчас, сколько потерялось,
и что было бы, если добавлять знак по звуку — при разных порогах.

    ..\\.venv\\Scripts\\python.exe qreport.py
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

QUESTION = {"question_wording", "question_intonation"}


def load(name: str):
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"нет файла {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="qlabels.json")
    ap.add_argument("--strict", action="store_true",
                    help="считать вопросом только то, где обе разметки сошлись")
    args = ap.parse_args()

    dump = {r["id"]: r for r in load("qeval-dump.json")}
    score = {r["id"]: r for r in load("qscore.json")}
    labels = load(args.labels)
    if isinstance(labels, dict):
        labels = labels.get("labels", [])

    rows = []
    for l in labels:
        rid = l.get("id", "")
        if rid not in dump:
            continue
        v1, v2 = l.get("verdict"), l.get("verdict2")
        q1 = v1 in QUESTION
        q2 = v2 in QUESTION if v2 else None
        if args.strict and q2 is not None and q1 != q2:
            continue
        d = dump[rid]
        s = score.get(rid)
        rows.append({
            "id": rid, "raw": d["raw"], "final": d["final"], "model": d["model"],
            "verdict": v1, "verdict2": v2,
            "is_q": q1, "wording": v1 == "question_wording",
            "heard": "?" in d["raw"], "got": "?" in d["final"],
            "d": s["d"] if s else None,
        })

    n = len(rows)
    qs = [r for r in rows if r["is_q"]]
    wording = [r for r in qs if r["wording"]]
    inton = [r for r in qs if not r["wording"]]
    print(f"надиктовок с разметкой: {n}")
    print(f"из них вопросов: {len(qs)}  "
          f"(видно по словам {len(wording)}, только по голосу {len(inton)})")
    if not qs:
        return

    heard = [r for r in qs if r["heard"]]
    got = [r for r in qs if r["got"]]
    print(f"распознавалка услышала вопрос: {len(heard)} из {len(qs)} "
          f"({len(heard)/len(qs)*100:.0f}%)")
    print(f"знак дошёл до человека:        {len(got)} из {len(qs)} "
          f"({len(got)/len(qs)*100:.0f}%)")
    false_now = [r for r in rows if not r["is_q"] and r["got"]]
    print(f"лишних знаков сейчас: {len(false_now)} "
          f"(на {n - len(qs)} не-вопросов)")

    lost = [r for r in qs if not r["got"]]
    print(f"\nпотеряно вопросов: {len(lost)}"
          f"  — по словам видно {sum(r['wording'] for r in lost)},"
          f" только по голосу {sum(not r['wording'] for r in lost)}")

    # --- что дал бы знак по звуку ---
    have = [r for r in rows if r["d"] is not None]
    print(f"\n=== добавлять «?» по звуку (есть оценка у {len(have)} надиктовок) ===")
    print("порог   поймано+   лишних+   стало поймано / всего   лишних всего")
    for t in (2.0, 1.0, 0.5, 0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -5.0):
        add_ok = [r for r in have if r["is_q"] and not r["got"] and r["d"] > t]
        add_bad = [r for r in have if not r["is_q"] and not r["got"] and r["d"] > t]
        now_ok = len([r for r in have if r["is_q"] and r["got"]])
        now_bad = len([r for r in have if not r["is_q"] and r["got"]])
        tot_q = len([r for r in have if r["is_q"]])
        print(f"{t:+5.1f}  {len(add_ok):8d} {len(add_bad):9d}"
              f"   {now_ok+len(add_ok):6d} / {tot_q:<10d} {now_bad+len(add_bad):6d}")

    print("\n=== вопросы, потерянные при пороге -1.0 (первые 15) ===")
    for r in [x for x in have if x["is_q"] and not x["got"] and x["d"] <= -1.0][:15]:
        print(f"  d={r['d']:+6.2f}  {r['final'][:110]}")
    print("\n=== лишние знаки при пороге -1.0 (первые 15) ===")
    for r in [x for x in have if not x["is_q"] and not x["got"] and x["d"] > -1.0][:15]:
        print(f"  d={r['d']:+6.2f}  {r['final'][:110]}")

    dis = [r for r in rows if r["verdict2"] and r["verdict"] != r["verdict2"]]
    print(f"\nразметчики разошлись: {len(dis)} из {n}")


if __name__ == "__main__":
    main()
