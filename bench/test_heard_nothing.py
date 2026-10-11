# -*- coding: utf-8 -*-
"""A quiet take with no speech is not recognized into a word (09.10.2026).

Quiet takes are pulled up before recognition, and the hiss of an empty one,
pulled up, was heard as "Yep." and pasted. stt.asr.heard_nothing looks at the
sound before it is pulled up. Synthetic noise only; nothing real is read.

    ..\\.venv\\Scripts\\python.exe test_heard_nothing.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import asr as A  # noqa: E402
from stt import audio as audio_mod  # noqa: E402


def hiss(seconds: float, peak: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 1.0, int(seconds * 16000)).astype(np.float32)
    return (x * (peak / float(np.abs(x).max()))).astype(np.float32)


def main() -> int:
    bad = 0

    def check(name: str, got: bool, want: bool) -> None:
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got}")

    quiet = hiss(2.0, 0.07)
    # The case itself: a quiet empty take is pulled up seven times or more.
    check("quiet hiss is pulled up", float(np.abs(audio_mod.normalize(quiet)).max()) > 0.4, True)
    check("quiet hiss: heard nothing", A.heard_nothing(quiet), True)
    check("empty take: not this rule", A.heard_nothing(np.zeros(0, np.float32)), False)

    # A loud take is not pulled up: the recognizer's own filter sees it as is,
    # so the extra check is skipped (and costs nothing on long loud takes).
    real_vad = A.speech_chunks
    calls = []
    A.speech_chunks = lambda audio: calls.append(len(audio)) or []
    try:
        check("loud take: not this rule", A.heard_nothing(hiss(2.0, 0.5)), False)
        check("loud take: speech filter not run", bool(calls), False)
        # Quiet speech the filter does find is kept.
        A.speech_chunks = lambda audio: [(4000, 20000)]
        check("quiet take with speech: kept", A.heard_nothing(quiet), False)
    finally:
        A.speech_chunks = real_vad

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
