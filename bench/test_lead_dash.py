# -*- coding: utf-8 -*-
"""A take must not open with a dialogue dash the recognizer wrote (02.10.2026).

    ..\\.venv\\Scripts\\python.exe test_lead_dash.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import strip_dialogue_dash  # noqa: E402

CASES = [
    ("— Значит, отчёт готов.", "Значит, отчёт готов."),
    ("  – Окей, запускай.", "Окей, запускай."),
    ("- Ага, это оно.", "Ага, это оно."),
    ("-5 градусов на улице.", "-5 градусов на улице."),
    ("Отчёт — это главное.", "Отчёт — это главное."),
    ("Окей — запускай.", "Окей — запускай."),
    ("", ""),
]

bad = 0
for said, want in CASES:
    got = strip_dialogue_dash(said)
    if got != want:
        bad += 1
        print(f"[X] {said!r} -> {got!r}, want {want!r}")
if bad:
    sys.exit(1)
print(f"[ok] leading dialogue dash: {len(CASES)}/{len(CASES)}")
