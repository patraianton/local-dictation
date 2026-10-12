# -*- coding: utf-8 -*-
"""A loop inside one word ("и" four hundred times) is a loop too: it is
caught, the take is read again with the penalty on repeated words, and if
every read still loops the wall of letters is cut down to three.

Invented phrases, a fake model: no GPU, no recordings.

    ..\\.venv\\Scripts\\python.exe test_letter_loop.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import LOOP_PENALTY, Asr, collapse_letter_loops, looped  # noqa: E402

bad = 0


def check(ok, what, detail=""):
    global bad
    bad += not ok
    print(f"[{'v' if ok else 'X'}] {what}" + ("" if ok else f"  ({detail})"))


WALL = "Ну, " + "о" * 300
CASES = [
    (WALL, True, "one letter three hundred times"),
    ("Да, " + "ла" * 40 + ".", True, "a two-letter unit forty times"),
    ("Ааааа, ну давай.", False, "a drawn-out «ааааа»"),
    ("Хммммм, не знаю.", False, "«хммммм»"),
    ("Ахахахаха, смешно.", False, "a short laugh"),
    ("Возьми коробку и отнеси на склад.", False, "an ordinary sentence"),
    ("Код 1000000 и всё.", False, "a number with six zeros"),
    ("полка, " * 14, True, "the old word-level loop still caught"),
]

CFG = {"asr": {"model": "large-v3-turbo", "short_model": "", "timestamps_after_s": 25.0,
               "prompt_style": "commands", "reread_early_end": False}}


class FakeModel:
    max_length = 448

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return [SimpleNamespace(text=self.answer(kw))], None


def main() -> int:
    for text, want, what in CASES:
        check(looped(text) is want, f"looped: {what}", looped(text))
    check(collapse_letter_loops(WALL) == "Ну, ооо", "a wall of letters is cut to three",
          collapse_letter_loops(WALL))
    check(collapse_letter_loops("Ааааа, ну давай.") == "Ааааа, ну давай.",
          "ordinary text is left alone")

    # the live path: loops without the penalty, reads right with it
    asr = Asr(CFG, ["Gamma"])
    right = "А, и... и... иди сам отнеси коробку."
    asr.model = FakeModel(lambda kw: right if kw["repetition_penalty"] >= LOOP_PENALTY
                          else "А, " + "и" * 400)
    asr.repetition_penalty = 1.0
    text, _ = asr.transcribe(np.zeros(16000 * 8, dtype=np.float32))
    check(text == right, "a letter loop is read again with the penalty", text[:60])
    check(len(asr.model.calls) == 2, "one extra read, not more", len(asr.model.calls))

    # every read loops: the wall is cut, the words around it stay
    asr.model = FakeModel(lambda kw: "Отнеси " + "и" * 400 + " коробку.")
    text, _ = asr.transcribe(np.zeros(16000 * 8, dtype=np.float32))
    check(text == "Отнеси иии коробку.", "nothing helps: the wall is cut down", text[:60])

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
