# -*- coding: utf-8 -*-
"""Сколько вопросов теряется: «Ты проверил?» превращается в «Ты проверил.»

В русском вопрос часто отличается от утверждения только интонацией, а
распознавалка склонна ставить точку. Считаем по всем записям Антона,
сравнивая с ElevenLabs.

    ..\\.venv\\Scripts\\python.exe questions.py
"""
import json
import re
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent / "eval-result.json"

# Слова, с которых почти всегда начинается вопрос.
ASK = {
    "почему", "зачем", "сколько", "какой", "какая", "какое", "какие", "каком",
    "какую", "какого", "каким", "где", "когда", "куда", "откуда", "кто", "кому",
    "чей", "как", "чего", "чём", "чем", "что", "разве", "неужели", "можешь",
    "можно", "надо", "нужно", "будет", "есть",
}
SENT = re.compile(r"[^.!?…]+[.!?…]*")
WORD = re.compile(r"[^\W_]+", re.UNICODE)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.findall(text or "") if s.strip()]


def is_q(s: str) -> bool:
    return s.rstrip().endswith("?")


def key(s: str) -> str:
    return " ".join(w.lower() for w in WORD.findall(s))


def main() -> None:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    recs = data["records"]

    for variant, res in data["variants"].items():
        lost = added = same = 0
        examples = []
        for rec, mine in zip(recs, res["texts"]):
            ref_map = {key(s): is_q(s) for s in sentences(rec["elevenlabs"])}
            for s in sentences(mine):
                k = key(s)
                if k not in ref_map or not k:
                    continue
                ref_q, my_q = ref_map[k], is_q(s)
                if ref_q and not my_q:
                    lost += 1
                    if len(examples) < 12:
                        examples.append(("потерян", s))
                elif my_q and not ref_q:
                    added += 1
                elif ref_q and my_q:
                    same += 1

        print(f"=== {variant} ===")
        print(f"  вопросов узнано верно: {same}")
        print(f"  вопрос потерян (стал точкой): {lost}")
        print(f"  точка стала вопросом:         {added}")
        if examples:
            print("  примеры потерянных:")
            for _, s in examples[:8]:
                print(f"    {s[:95]}")
        print()

    # Сколько из потерянных можно поймать по вопросительному слову в начале.
    first_variant = next(iter(data["variants"]))
    catchable = total = 0
    for rec, mine in zip(recs, data["variants"][first_variant]["texts"]):
        ref_map = {key(s): is_q(s) for s in sentences(rec["elevenlabs"])}
        for s in sentences(mine):
            k = key(s)
            if k in ref_map and ref_map[k] and not is_q(s):
                total += 1
                words = WORD.findall(s.lower())
                if words and words[0] in ASK:
                    catchable += 1
    print(f"из {total} потерянных вопросов начинаются с вопросительного слова: {catchable}")


if __name__ == "__main__":
    main()
