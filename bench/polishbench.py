# -*- coding: utf-8 -*-
"""Очная ставка моделей-корректоров на настоящих расшифровках Антона.

Берём сырой текст распознавалки из eval-result.json, прогоняем через
каждую модель в LM Studio и смотрим: сколько секунд и что получилось.

    ..\\.venv\\Scripts\\python.exe polishbench.py [сколько] [вариант]
"""
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt.fixes import Fixes  # noqa: E402
from stt.polish import (  # noqa: E402
    SYSTEM_LIGHT,
    _strip_think,
    _strip_wrapping,
    allowed_words,
    constrain,
)

BASE = "http://127.0.0.1:1234"
EVAL = Path(__file__).resolve().parent / "eval-result.json"
OUT = Path(__file__).resolve().parent / "polish-result.md"

CANDIDATES = [
    "qwen3-4b-instruct-2507",
    "qwen/qwen3-30b-a3b-2507",
    "gemma-3-12b-it",
]


def models() -> list[str]:
    r = httpx.get(f"{BASE}/v1/models", timeout=10)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", []) if "embed" not in m["id"].lower()]


def ask(model: str, system: str, text: str, timeout: float = 90.0) -> tuple[str, float]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "max_tokens": min(1200, int(len(text) / 2) + 100),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    out = r.json()["choices"][0]["message"]["content"]
    return _strip_wrapping(_strip_think(out)), time.perf_counter() - t0


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    variant = sys.argv[2] if len(sys.argv) > 2 else "turbo + пример + защита"

    data = json.loads(EVAL.read_text(encoding="utf-8"))
    recs = data["records"]
    texts = data["variants"][variant]["texts"]

    fixes = Fixes(cfg_mod.FIXES_PATH)
    terms = cfg_mod.glossary()
    allowed = allowed_words(terms, fixes)
    system = SYSTEM_LIGHT + "\n\nСписок названий и терминов:\n" + ", ".join(terms[:150])

    # Берём реплики ТИПИЧНОЙ длины (у Антона медиана ~7 секунд), а не самые
    # длинные: иначе замер времени врёт в худшую сторону.
    typical = [i for i, r in enumerate(recs) if 4.0 <= r["seconds"] <= 12.0 and texts[i]]
    step = max(1, len(typical) // n)
    idx = typical[::step][:n]

    available = models()
    print("в LM Studio доступны:", available)
    picked = [m for m in CANDIDATES if m in available] or available[:1]
    print("проверяю:", picked, "\n")

    lines = ["# Корректоры на настоящих расшифровках", "",
             f"Вариант распознавания: **{variant}**", ""]
    stats = {}
    for model in picked:
        print(f"=== {model} ===")
        # прогрев (первый запрос грузит модель в память видеокарты)
        try:
            _, warm = ask(model, system, "проверка связи", timeout=300)
            print(f"  загрузка+прогрев: {warm:.1f} с")
        except Exception as exc:
            print(f"  НЕ ПОДНЯЛАСЬ: {exc}")
            continue
        times, outs = [], []
        for i in idx:
            raw = texts[i]
            pre, _ = fixes.apply(raw)
            try:
                out, took = ask(model, system, pre)
                locked = constrain(pre, out, allowed)
            except Exception as exc:
                out = locked = f"[СБОЙ {type(exc).__name__}]"
                took = 0.0
            times.append(took)
            outs.append((i, pre, out, locked))
        stats[model] = {"avg": sum(times) / len(times), "max": max(times),
                        "warm": warm, "outs": outs}
        print(f"  среднее {stats[model]['avg']:.2f} с, худшее {stats[model]['max']:.2f} с\n")

    lines.append("## Скорость")
    lines.append("")
    for model, s in stats.items():
        lines.append(f"- **{model}**: в среднем {s['avg']:.2f} с, худшее {s['max']:.2f} с "
                     f"(загрузка {s['warm']:.0f} с)")
    lines.append("")
    lines.append("---")
    lines.append("")

    for k, i in enumerate(idx):
        lines.append(f"## {k+1}. {recs[i]['seconds']:.0f} с речи")
        lines.append("")
        lines.append(f"**ElevenLabs:**  \n{recs[i]['elevenlabs']}")
        lines.append("")
        first = True
        for model, s in stats.items():
            _, pre, out, locked = s["outs"][k]
            if first:
                lines.append(f"**распознала:**  \n{pre}")
                lines.append("")
                first = False
            mark = "" if out == locked else "  _(замок откатил лишнее)_"
            lines.append(f"**{model}:**{mark}  \n{locked}")
            if out != locked:
                lines.append(f"<sub>без замка было: {out}</sub>")
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"подробности: {OUT}")


if __name__ == "__main__":
    main()
