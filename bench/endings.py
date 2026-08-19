# -*- coding: utf-8 -*-
"""Hunting for inverted meaning: "do it" heard as "I will do it".

Compares our transcript with the ElevenLabs one and keeps only the differences
where the words differ by their ending alone — that is exactly an order turned
into a promise.

    ..\\.venv\\Scripts\\python.exe endings.py
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL = Path(__file__).resolve().parent / "eval-result.json"
WORD = re.compile(r"[^\W_]+", re.UNICODE)


def same_stem(a: str, b: str, keep: int = 4) -> bool:
    a, b = a.lower(), b.lower()
    if a == b or len(a) < keep + 1 or len(b) < keep + 1:
        return False
    return a[:keep] == b[:keep]


def main() -> None:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    recs = data["records"]
    variant = next(iter(data["variants"]))
    ours = data["variants"][variant]["texts"]
    print(f"вариант: {variant}, записей: {len(recs)}\n")

    hits = []
    for rec, mine in zip(recs, ours):
        a = WORD.findall(rec["elevenlabs"])
        b = WORD.findall(mine)
        sm = difflib.SequenceMatcher(
            a=[w.lower() for w in a], b=[w.lower() for w in b], autojunk=False
        )
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
                continue
            ref, got = a[i1], b[j1]
            if same_stem(ref, got):
                hits.append((ref, got, rec["elevenlabs"][:110]))

    # Только те, где меняется лицо: повелительное <-> «я сделаю».
    def is_flip(ref: str, got: str) -> bool:
        r, g = ref.lower(), got.lower()
        return (r.endswith(("й", "ь", "и")) and g.endswith(("ю", "у"))) or (
            g.endswith(("й", "ь", "и")) and r.endswith(("ю", "у"))
        )

    flips = [h for h in hits if is_flip(h[0], h[1])]
    print(f"расхождений в одну букву-окончание: {len(hits)}")
    print(f"из них меняют лицо глагола:        {len(flips)}\n")
    for ref, got, ctx in flips:
        print(f"  ElevenLabs «{ref}»  ->  наша «{got}»")
        print(f"      …{ctx.strip()}…")


if __name__ == "__main__":
    main()
