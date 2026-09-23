# -*- coding: utf-8 -*-
"""Почему в надиктовках нет вопросительных знаков.

Берёт настоящие надиктовки из журнала, прогоняет их через правщик ещё раз и
сохраняет ТРИ текста для каждой:

    raw    — что услышала распознавалка (в журнале есть);
    model  — что вернул правщик до всех наших запретов (в журнале НЕТ,
             ради этого и нужен прогон);
    final  — что получил человек (в журнале есть).

Имея все три, можно посчитать, на каком шаге пропадает вопрос: распознавалка
не услышала, правщик не поставил, или поставил, а наши правила сняли.

Правщик берётся тот же, что у диктовки: модель, которая лежит в памяти LM
Studio. Запросы идут строго по одному и с паузой — чтобы не отбирать модель
у живой диктовки, если человек в это время говорит.

    ..\\.venv\\Scripts\\python.exe qeval.py 2026-09-08 2026-09-10
    ..\\.venv\\Scripts\\python.exe qeval.py 2026-09-08 2026-09-10 --out дамп.json
"""
import argparse
import glob
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt import polish as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "qeval-dump.json"


def takes(since: str, until: str) -> list[dict]:
    rows = []
    for p in sorted(glob.glob(str(ROOT / "logs" / "2026-*.jsonl"))):
        day = Path(p).stem
        if not (since <= day <= until):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("raw", "").strip():
                rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("since")
    ap.add_argument("until")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--pause", type=float, default=0.05,
                    help="пауза между запросами, чтобы не мешать живой диктовке")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    terms = cfg_mod.glossary()
    fixes = None
    try:
        from stt.fixes import Fixes

        fixes = Fixes(cfg_mod.FIXES_PATH)
    except Exception:
        pass
    pol = P.Polisher(cfg, terms, fixes)
    if not pol.check(force=True):
        print(f"[X] правщик недоступен: {pol.reason}")
        sys.exit(1)
    print(f"[.] правщик: {pol.model or '(та, что в памяти)'}")

    rows = takes(args.since, args.until)
    print(f"[.] надиктовок за {args.since}..{args.until}: {len(rows)}")

    out, t0 = [], time.perf_counter()
    for n, r in enumerate(rows, 1):
        raw = r["raw"]
        model_text = ""
        note = ""
        if len(raw.split()) < pol.min_words:
            note = "too short, skipped"
        else:
            body = {
                "model": pol.model,
                "messages": [
                    {"role": "system", "content": pol.system_prompt},
                    {"role": "user", "content": raw},
                ],
                "temperature": 0.0,
                "max_tokens": min(1200, int(len(raw) / 2) + 100),
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            try:
                resp = pol._client.post(
                    f"{pol.base}/v1/chat/completions", json=body, timeout=20.0
                )
                resp.raise_for_status()
                model_text = P._strip_wrapping(
                    P._strip_think(resp.json()["choices"][0]["message"]["content"])
                )
            except Exception as exc:
                note = f"failed: {type(exc).__name__}"
        out.append({
            "id": r.get("id", ""),
            "time": r.get("time", ""),
            "seconds_audio": r.get("seconds_audio", 0),
            "raw": raw,
            "model": model_text,
            "final": r.get("final", ""),
            "note": note or r.get("polish_note", ""),
        })
        if n % 25 == 0 or n == len(rows):
            print(f"    {n}/{len(rows)}  ({time.perf_counter()-t0:.0f} c)")
        if args.pause:
            time.sleep(args.pause)

    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[v] сохранено: {args.out}")


if __name__ == "__main__":
    main()
