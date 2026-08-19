# -*- coding: utf-8 -*-
"""Fixing the person of a verb where the data for it exists: in the audio.

The corrector model cannot hear the audio and simply guesses from the phrasing
of the question. The recognizer can hear it. Checks whether the imperative hint
pulls its weight.

    ..\\.venv\\Scripts\\python.exe test_prompt_person.py
"""
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt import cuda_fix  # noqa: E402

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402

EVAL = Path(__file__).resolve().parent / "eval-result.json"

# Куски расшифровок, по которым находим нужные записи, и что должно выйти.
WANT_IMPERATIVE = [
    ("Сделай session handover", "сделай"),
    ("то, что оборвалось, запускай", "запускай"),
    ("отправляй opus панели", "отправляй"),
    ("отгружай одну за одной", "отгружай"),
    ("Продолжай", "продолжай"),
]
WANT_SELF = [
    ("После отпуска схожу и посмотрю", "посмотрю"),
    ("Запускаю запись контента", "запускаю"),
    ("добавлю тебе, пока не забыл", "добавлю"),
    ("Ещё добавлю тебе", "добавлю"),
]

TERMS = ", ".join(cfg_mod.glossary()[:45])

PROMPTS = {
    "как сейчас (термины)": f"Термины и названия: {TERMS}.",
    "термины + приказы": (
        "Сделай, проверь, посмотри, запусти, запускай, отправляй, продолжай, "
        "отгружай, собери, поставь, обнови, покажи, найди, добавь, открой. "
        f"Термины и названия: {TERMS}."
    ),
    "приказы фразами + термины": (
        "Сделай session handover. Продолжай, не останавливайся. "
        "Запускай то, что оборвалось. Отправляй агентов и собери отчёт. "
        "Проверь Intercom и обнови дашборд. "
        f"Термины и названия: {TERMS}."
    ),
}


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        sr, n_ch = wf.getframerate(), wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    a = pcm.astype(np.float32) / 32768.0
    if n_ch > 1:
        a = a.reshape(-1, n_ch).mean(axis=1)
    if sr != 16000:
        import soxr

        a = soxr.resample(a, sr, 16000).astype(np.float32)
    return a


def main() -> None:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    recs = data["records"]

    def find(fragment: str):
        key = re.sub(r"\W+", " ", fragment.lower()).strip()
        for r in recs:
            hay = re.sub(r"\W+", " ", r["elevenlabs"].lower())
            if key in hay and Path(r["wav"]).exists():
                return r
        return None

    cases = []
    for frag, want in WANT_IMPERATIVE:
        r = find(frag)
        if r:
            cases.append((r, want, True))
    for frag, want in WANT_SELF:
        r = find(frag)
        if r:
            cases.append((r, want, False))
    print(f"нашёл записей: {len(cases)} "
          f"({sum(1 for c in cases if c[2])} приказов, "
          f"{sum(1 for c in cases if not c[2])} о себе)\n")
    if not cases:
        print("не нашёл ни одной записи — нечего проверять")
        return

    audios = [load_wav(r["wav"]) for r, _, _ in cases]
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    model.transcribe(np.zeros(16000, dtype=np.float32), language="ru", beam_size=1)

    for name, prompt in PROMPTS.items():
        good = 0
        details = []
        for (rec, want, is_imp), audio in zip(cases, audios):
            segs, _ = model.transcribe(
                audio, language="ru", beam_size=5,
                temperature=[0.0, 0.2, 0.4, 0.6],
                compression_ratio_threshold=2.4, repetition_penalty=1.15,
                condition_on_previous_text=False, initial_prompt=prompt,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                without_timestamps=True,
            )
            text = " ".join(s.text.strip() for s in segs).strip()
            ok = re.search(rf"(?<!\w){want}(?!\w)", text, re.IGNORECASE) is not None
            good += ok
            if not ok:
                details.append(f"      [{'приказ' if is_imp else 'о себе'}] "
                               f"ждали «{want}»: {text[:90]}")
        print(f"{name:<26} {good} из {len(cases)}")
        for d in details:
            print(d)
        print()


if __name__ == "__main__":
    main()
