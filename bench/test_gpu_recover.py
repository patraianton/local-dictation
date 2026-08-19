# -*- coding: utf-8 -*-
r"""Checks that the app gets back up after the GPU falls away.

On 2026-08-19 the machine slept, the GPU context died, and the app spent four
days writing "FAILED" for every take until it was restarted by hand.

    ..\.venv\Scripts\python.exe test_gpu_recover.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.asr import Asr  # noqa: E402
# Импорт наверху не случайно: stt.__main__ при загрузке подменяет поток
# вывода, и всё, что напечатано до него, пропадает.
from stt.__main__ import Dictation  # noqa: E402


class FakeAsr(Asr):
    """Распознавалка, которая падает заданное число раз, потом чинится."""

    def __init__(self, fails: int, exc: Exception, reload_ok: bool = True):
        self.fails = fails
        self.exc = exc
        self.reload_ok = reload_ok
        self.reloads = 0
        self.device = "cuda"
        self.model = object()
        self.model_name = "fake"
        self.compute_type = "float16"

    def transcribe(self, audio):
        if self.fails > 0:
            self.fails -= 1
            raise self.exc
        return "услышанный текст", 0.01

    def reload(self):
        self.reloads += 1
        if self.reload_ok:
            self.device = "cuda"
            self.model = object()
            return "cuda"
        self.device = "cpu"
        self.model = object()
        return "cpu"


class Holder:
    """Только та часть программы, которая нас интересует."""

    def __init__(self, asr):
        self.asr = asr
        self.said = []

    class _Hud:
        def set(self, *a, **k):
            pass

    hud = _Hud()


def make_holder(asr):
    h = Holder(asr)
    h.transcribe_resilient = Dictation.transcribe_resilient.__get__(h, Holder)
    return h


CUDA_ERRORS = [
    RuntimeError("CUDA failed with error unknown error"),
    RuntimeError("cuBLAS failed with status CUBLAS_STATUS_NOT_INITIALIZED"),
    RuntimeError("cudnn error"),
    RuntimeError("CUDA out of memory"),
]


def main() -> None:
    bad = 0
    audio = np.zeros(16000, dtype=np.float32)

    def check(ok, why, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok and detail:
            print(f"      {detail}")

    # 1. каждая известная поломка видеокарты опознаётся
    for exc in CUDA_ERRORS:
        check(Asr.looks_like_lost_gpu(exc), f"опознаёт поломку: {str(exc)[:44]}")

    # 2. обычная ошибка НЕ считается поломкой видеокарты
    check(not Asr.looks_like_lost_gpu(ValueError("плохой звук")),
          "обычную ошибку за поломку видеокарты не принимает")

    # 3. упала один раз — поднялись и распознали
    asr = FakeAsr(fails=1, exc=CUDA_ERRORS[0])
    h = make_holder(asr)
    text, _t = h.transcribe_resilient(audio)
    check(text == "услышанный текст" and asr.reloads == 1,
          "после отвала видеокарты модель поднимается и текст доходит",
          f"текст={text!r}, перезагрузок={asr.reloads}")

    # 4. видеокарта не вернулась — уходим на процессор, но работаем
    asr = FakeAsr(fails=1, exc=CUDA_ERRORS[0], reload_ok=False)
    h = make_holder(asr)
    text, _t = h.transcribe_resilient(audio)
    check(text == "услышанный текст" and asr.device == "cpu",
          "видеокарта не вернулась — переходим на процессор и продолжаем",
          f"текст={text!r}, устройство={asr.device}")

    # 5. ошибка не про видеокарту — пробрасываем как есть, не маскируем
    asr = FakeAsr(fails=1, exc=ValueError("плохой звук"))
    h = make_holder(asr)
    try:
        h.transcribe_resilient(audio)
        check(False, "чужую ошибку не проглатывает")
    except ValueError:
        check(asr.reloads == 0, "чужую ошибку не проглатывает и модель не трогает")

    # 6. если и после перезагрузки падает — ошибка видна, а не тишина
    asr = FakeAsr(fails=5, exc=CUDA_ERRORS[0])
    h = make_holder(asr)
    try:
        h.transcribe_resilient(audio)
        check(False, "не молчит, если починиться не удалось")
    except RuntimeError:
        check(asr.reloads == 1, "не молчит, если починиться не удалось")

    print(f"\n{'all passed' if not bad else str(bad) + ' failed'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
