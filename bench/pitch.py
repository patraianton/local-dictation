# -*- coding: utf-8 -*-
"""Can a question be heard in the voice: is there a difference in pitch.

In text, "Мы заливаем статьи уже?" cannot be told from the statement — the only
difference is intonation. Checked on real recordings labelled by ElevenLabs:
is the voice really doing something different at the end of a question.

    ..\\.venv\\Scripts\\python.exe pitch.py
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent / "eval-result.json"

SR = 16000
FRAME = 640          # 40 мс
HOP = 160            # 10 мс
F0_MIN, F0_MAX = 70, 350
LAG_MIN, LAG_MAX = SR // F0_MAX, SR // F0_MIN


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        sr, n_ch = wf.getframerate(), wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    a = pcm.astype(np.float32) / 32768.0
    if n_ch > 1:
        a = a.reshape(-1, n_ch).mean(axis=1)
    if sr != SR:
        import soxr

        a = soxr.resample(a, sr, SR).astype(np.float32)
    return a


def f0_track(audio: np.ndarray) -> np.ndarray:
    """Высота голоса по кадрам. NaN там, где голоса нет."""
    n = 1 + max(0, (len(audio) - FRAME) // HOP)
    out = np.full(n, np.nan, dtype=np.float32)
    if n <= 0:
        return out
    win = np.hanning(FRAME).astype(np.float32)
    for i in range(n):
        frame = audio[i * HOP: i * HOP + FRAME]
        if len(frame) < FRAME:
            break
        frame = (frame - frame.mean()) * win
        energy = float(np.dot(frame, frame))
        if energy < 1e-4:
            continue
        # автокорреляция через БПФ
        size = 1 << (2 * FRAME - 1).bit_length()
        spec = np.fft.rfft(frame, size)
        corr = np.fft.irfft(spec * np.conj(spec), size)[:LAG_MAX + 1]
        seg = corr[LAG_MIN:LAG_MAX + 1]
        if seg.size == 0:
            continue
        lag = int(np.argmax(seg)) + LAG_MIN
        if corr[lag] / (corr[0] + 1e-9) < 0.35:      # не похоже на голос
            continue
        out[i] = SR / lag
    return out


def features(audio: np.ndarray) -> dict | None:
    f0 = f0_track(audio)
    voiced = f0[~np.isnan(f0)]
    if voiced.size < 20:
        return None
    med = float(np.median(voiced))
    idx = np.where(~np.isnan(f0))[0]
    tail = idx[idx >= idx[-1] - 100]                  # последняя секунда
    tail_f0 = f0[tail]
    tail_f0 = tail_f0[~np.isnan(tail_f0)]
    if tail_f0.size < 5:
        return None
    last = f0[idx[-15:]]
    last = last[~np.isnan(last)]
    return {
        # насколько голос в конце выше обычного
        "tail_peak": float(np.max(tail_f0) / med),
        "tail_mean": float(np.mean(tail_f0) / med),
        "very_end": float(np.mean(last) / med) if last.size else 1.0,
        # подъём внутри последней секунды
        "rise": float(np.max(tail_f0) / (np.min(tail_f0) + 1e-6)),
    }


def main() -> None:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    recs = data["records"]

    q, s = [], []
    for r in recs:
        text = (r["elevenlabs"] or "").strip()
        if not text or not Path(r["wav"]).exists():
            continue
        # только однозначные случаи: вся реплика — один вопрос или одно утверждение
        if text.count("?") + text.count(".") + text.count("!") > 1:
            continue
        if r["seconds"] < 1.0 or r["seconds"] > 12:
            continue
        f = features(load_wav(r["wav"]))
        if not f:
            continue
        (q if text.endswith("?") else s).append(f)

    print(f"вопросов: {len(q)}, утверждений: {len(s)}\n")
    if len(q) < 8 or len(s) < 8:
        print("маловато размеченных записей, вывод делать не на чем")
        return

    print(f"{'признак':<12} {'вопросы':>10} {'утвержд.':>10} {'разница':>9}")
    for name in ("tail_peak", "tail_mean", "very_end", "rise"):
        a = np.array([x[name] for x in q])
        b = np.array([x[name] for x in s])
        # насколько классы разъезжаются: разница средних в долях разброса
        d = (a.mean() - b.mean()) / (np.sqrt((a.var() + b.var()) / 2) + 1e-9)
        print(f"{name:<12} {a.mean():>10.3f} {b.mean():>10.3f} {d:>9.2f}")

    print("\nразница: 0 — не отличить, 0.5 — слабо, 0.8+ — уже можно ловить")

    best = "tail_peak"
    a = np.array([x[best] for x in q])
    b = np.array([x[best] for x in s])
    print(f"\nподбор порога по «{best}»:")
    for thr in np.arange(1.05, 1.9, 0.05):
        caught = (a > thr).sum()
        false = (b > thr).sum()
        print(f"  порог {thr:.2f}: поймано вопросов {caught}/{len(a)}, "
              f"ложных {false}/{len(b)}")


if __name__ == "__main__":
    main()
