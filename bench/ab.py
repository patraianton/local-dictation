# -*- coding: utf-8 -*-
"""Очная ставка на настоящем голосе Антона.

Берём записи из истории Spokenly и прогоняем через локальные модели.
Рядом — то, что выдал ElevenLabs Scribe (за который Антон платит).

    ..\\.venv\\Scripts\\python.exe ab.py [сколько_записей]
"""
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cuda_fix  # noqa: E402
from stt import config as cfg_mod  # noqa: E402

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402
from spokenly import records  # noqa: E402

OUT = Path(__file__).resolve().parent / "ab-result.md"


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"неожиданная разрядность {width*8} бит")
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        pcm = pcm.reshape(-1, n_ch).mean(axis=1)
    if sr != 16000:
        import soxr

        pcm = soxr.resample(pcm, sr, 16000).astype(np.float32)
    return pcm


def pick(recs: list[dict], n: int) -> list[dict]:
    """Берём реплики рабочей длины, равномерно по всей истории."""
    good = [r for r in recs if r["wav"] and 3.0 <= r["seconds"] <= 25.0 and r["text"]]
    good.sort(key=lambda r: r["created"])
    if len(good) <= n:
        return good
    step = len(good) / n
    return [good[int(i * step)] for i in range(n)]


def transcribe_all(model_name: str, items, prompt: str, beam: int = 1):
    model = WhisperModel(model_name, device="cuda", compute_type="float16")
    # прогрев
    model.transcribe(np.zeros(16000, dtype=np.float32), language="ru", beam_size=beam)
    out = []
    for audio in items:
        t0 = time.perf_counter()
        segs, _ = model.transcribe(
            audio,
            language="ru",
            beam_size=beam,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segs).strip()
        out.append((text, time.perf_counter() - t0))
    del model
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    recs = pick(records(), n)
    print(f"взято {len(recs)} записей, суммарно {sum(r['seconds'] for r in recs):.0f} с речи")

    audios = []
    for r in recs:
        audios.append(load_wav(r["wav"]))

    terms = cfg_mod.glossary()
    from stt.asr import build_prompt

    prompt = build_prompt(terms, 45)

    variants = {
        "large-v3": ("large-v3", 1, prompt),
        "turbo": ("large-v3-turbo", 1, prompt),
        "turbo-без-подсказки": ("large-v3-turbo", 1, ""),
    }
    results = {}
    for label, (model_name, beam, pr) in variants.items():
        print(f"гоню {label}...")
        results[label] = transcribe_all(model_name, audios, pr, beam)

    lines = ["# Очная ставка на голосе Антона", ""]
    lines.append(f"Записей: {len(recs)}. Аудио из истории Spokenly.")
    lines.append("")
    for label in variants:
        times = [t for _, t in results[label]]
        per_sec = sum(times) / max(1e-9, sum(r["seconds"] for r in recs))
        lines.append(
            f"- **{label}**: всего {sum(times):.1f} с на {sum(r['seconds'] for r in recs):.0f} с речи "
            f"(в среднем {sum(times)/len(times):.2f} с на реплику, "
            f"{per_sec*100:.0f}% от длительности)"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, r in enumerate(recs):
        lines.append(f"## {i+1}. {r['date']} — {r['seconds']:.1f} с")
        lines.append("")
        lines.append(f"**ElevenLabs (то, что ты видел):**  \n{r['text']}")
        lines.append("")
        for label in variants:
            text, took = results[label][i]
            lines.append(f"**{label}** ({took:.2f} с):  \n{text}")
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nготово: {OUT}")

    total_speech = sum(r["seconds"] for r in recs)
    print()
    for label in variants:
        times = [t for _, t in results[label]]
        print(f"{label:<22} {sum(times):6.1f} с всего | "
              f"{sum(times)/len(times):.2f} с на реплику | "
              f"{sum(times)/total_speech*100:4.0f}% от длительности речи")


if __name__ == "__main__":
    main()
