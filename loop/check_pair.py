# -*- coding: utf-8 -*-
"""Before a pair goes into fixes.tsv: where does the wrong form occur in all takes?

    python loop/check_pair.py "wrong form" ["another form" ...]

Prints, for each form, every take where it occurs as a whole word (any case)
in the recognized, the replaced or the pasted text, with the words around it,
so a form that is sometimes right as it is (a real word, another meaning) is
caught before the pair changes it everywhere.
"""
import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    forms = [f for f in sys.argv[1:] if f.strip()]
    pats = {f: re.compile(r"(?<!\w)" + re.escape(f) + r"(?!\w)", re.IGNORECASE) for f in forms}
    hits = {f: [] for f in forms}
    total = 0
    for jf in sorted((APP / "logs").glob("????-??-??.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            total += 1
            for f, p in pats.items():
                for field in ("raw", "after_fixes", "final"):
                    t = r.get(field) or ""
                    m = p.search(t)
                    if m:
                        a, b = max(0, m.start() - 60), m.end() + 60
                        hits[f].append((r.get("id"), field, t[a:b].replace("\n", " ")))
                        break
    print(f"{total} takes searched")
    for f in forms:
        print(f"\n== {f!r}: {len(hits[f])} takes")
        for rid, field, ctx in hits[f][-40:]:
            print(f"  {rid} [{field}] …{ctx}…")


if __name__ == "__main__":
    main()
