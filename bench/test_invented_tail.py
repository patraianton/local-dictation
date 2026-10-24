# -*- coding: utf-8 -*-
"""A made-up line in the sliver of sound at the end of a long take is cut (24.10.2026).

    ..\\.venv\\Scripts\\python.exe test_invented_tail.py
"""
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import cut_invented_tail  # noqa: E402

Seg = namedtuple("Seg", "start end text")
FIRST = Seg(0.27, 30.38, "Короче, собери отчёт по складу и пришли мне таблицу с остатками.")

CASES = [
    # The times of the live take of 24.10.2026 (30.63 s) and of the Mac's read
    # of it (31.15 s); the texts are made up, the shapes are the ones that came.
    ("a sign-off line in 0.25 s", [FIRST, Seg(30.40, 30.60, "Ставьте лайки и подписывайтесь на канал, ставьте лайки.")], 30.63, 1),
    ("a credits line in 0.77 s", [FIRST, Seg(30.40, 30.94, "Субтитры создавал Иван Петров")], 31.15, 1),
    ("a real last word in the sliver stays", [FIRST, Seg(30.40, 30.90, "Да.")], 31.15, 0),
    ("a real short phrase in a second and a half stays", [FIRST, Seg(30.40, 31.80, "Ну и всё, давай.")], 31.90, 0),
    ("a last window of real length is never judged", [FIRST, Seg(30.40, 33.0, "И ещё одна длинная фраза в самом конце записи.")], 33.4, 0),
    ("one segment alone is left to the other checks", [Seg(0.0, 0.6, "Ставьте лайки и подписывайтесь на канал, ставьте лайки.")], 0.8, 0),
    ("two made-up lines in a row: both go", [FIRST, Seg(30.40, 30.50, "Продолжение следует в следующей серии."), Seg(30.5, 30.6, "Спасибо за просмотр, ставьте лайки.")], 30.63, 2),
]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for name, segs, total, want in CASES:
        kept, cut = cut_invented_tail(segs, total)
        got = len(segs) - len(kept)
        ok = got == want and (bool(cut) == bool(want))
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> cut {got}, want {want}: {cut!r}"))
    print("all good" if not bad else f"failed: {bad}")
    sys.exit(1 if bad else 0)
