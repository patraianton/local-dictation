# -*- coding: utf-8 -*-
r"""Comparing recognizer settings: what is faster, and by how much.

    ..\.venv\Scripts\python.exe compare.py sample-en.wav
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cuda_fix  # noqa: E402

cuda_fix.enable()

from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402

LANG = "ru"
RUNS = 3


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    if sr != 16000:
        import soxr

        audio = soxr.resample(audio, sr, 16000).astype(np.float32)
    return audio


def run(name: str, model_name: str, batched: bool, beam: int, audio: np.ndarray):
    t0 = time.perf_counter()
    model = WhisperModel(model_name, device="cuda", compute_type="float16")
    load_s = time.perf_counter() - t0

    engine = BatchedInferencePipeline(model=model) if batched else model
    kw = dict(
        language=LANG,
        beam_size=beam,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    if batched:
        kw["batch_size"] = 8

    times, text = [], ""
    for i in range(RUNS + 1):  # первый прогон — прогрев
        t = time.perf_counter()
        segs, _ = engine.transcribe(audio, **kw)
        text = " ".join(s.text.strip() for s in segs).strip()
        dt = time.perf_counter() - t
        if i:
            times.append(dt)

    best, avg = min(times), sum(times) / len(times)
    print(f"{name:<34} загрузка {load_s:4.1f} с | лучшее {best:5.2f} с | среднее {avg:5.2f} с")
    del engine, model
    import gc

    import torch  # noqa: F401  (может не стоять — не страшно)

    gc.collect()
    return best, text


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "sample-en.wav"
    audio = load_wav(path)
    print(f"файл: {len(audio)/16000:.1f} с речи, {RUNS} замера на вариант\n")

    variants = [
        ("large-v3, луч 1", "large-v3", False, 1),
        ("large-v3, луч 1, пачками", "large-v3", True, 1),
        ("large-v3, луч 5", "large-v3", False, 5),
        ("large-v3-turbo, луч 1", "large-v3-turbo", False, 1),
        ("large-v3-turbo, луч 1, пачками", "large-v3-turbo", True, 1),
        ("large-v3-turbo, луч 5", "large-v3-turbo", False, 5),
    ]
    texts = {}
    for name, model_name, batched, beam in variants:
        try:
            _, text = run(name, model_name, batched, beam, audio)
            texts[name] = text
        except Exception as exc:
            print(f"{name:<34} НЕ ВЫШЛО: {type(exc).__name__}: {exc}")

    print("\n--- что услышал каждый вариант ---")
    for name, text in texts.items():
        print(f"\n[{name}]\n{text}")


if __name__ == "__main__":
    main()
