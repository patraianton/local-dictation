# -*- coding: utf-8 -*-
"""Long takes read by a cloud recognizer, ElevenLabs Scribe (31.10.2026).

Why. On 200 takes from 27-30.10.2026, read both ways and judged blind (six judges,
neither side named), the cloud made 19 errors on the 50 takes of 20 s and more
against 35 here, and answered sooner (2.1 s against 2.5 s). On takes under 5 s it
was no better and twice as slow; 5-20 s, a little better and 0.4 s slower. So
only long takes go out, by [cloud] min_seconds; anything that fails — no key, no
network, a slow answer, an empty text — and the take is read here as before.

The speaker's terms go along as `keyterms`. The cloud writes what it hears,
fillers and drawn-out vowels too ("а-а-а", "емууу"): those are taken out here,
the dictionary and the corrector come after, the same as for a take read here.
"""
import io
import os
import re
import time
import wave
from pathlib import Path

import httpx
import numpy as np

URL = "https://api.elevenlabs.io/v1/speech-to-text"
# The service refuses a term of 50 characters or more (measured 30.10.2026).
TERM_MAX_CHARS = 49
TERMS_MAX = 100

# "а-а-а", "э-э-э", "м-м", "эм", "мм", "uh", "um" — a sound, not a word.
FILLER_RE = re.compile(
    r"(?<!\w)(?:[аэмуеои](?:-[аэмуеои])+|э+м+|м{2,}|uh|um|hmm|mm)(?!\w)[,.]?\s*", re.I)
# A letter drawn out three times or more at the end of a word: "емууу" -> "ему".
STRETCH_RE = re.compile(r"(\w)\1{2,}\b")
# A word broken off with a double dash: "простой--" -> "простой".
BROKEN_RE = re.compile(r"(\w)--(?=\s|$)")


def clean(text: str) -> str:
    """The cloud's verbatim text, the way a take read here comes out."""
    t = FILLER_RE.sub("", text)
    t = STRETCH_RE.sub(r"\1", t)
    t = BROKEN_RE.sub(r"\1", t)
    t = re.sub(r"\s+([,.!?…])", r"\1", re.sub(r"\s{2,}", " ", t))
    t = re.sub(r",(?=\s*[,.!?…])", "", t).strip(" ,")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if t and t[-1] not in ".!?…":
        t += "."
    return t


def read_key(cfg_c: dict) -> str:
    """The key: the environment first, then the file named in the settings."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if key:
        return key
    path = Path(os.path.expanduser(str(cfg_c.get("key_file", "~/.secrets/speech-cloud.env"))))
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ELEVENLABS_API_KEY" and value.strip():
                return value.strip()
    except OSError:
        pass
    return ""


def wav_bytes(audio: np.ndarray, sr: int = 16000) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Cloud:
    def __init__(self, cfg: dict):
        c = cfg.get("cloud", {}) or {}
        self.enabled = bool(c.get("enabled", False))
        self.min_seconds = float(c.get("min_seconds", 20.0))
        self.model = str(c.get("model", "scribe_v2"))
        # Wait this long, plus this much per second of speech; then read here.
        # The slowest of 50 long takes answered in 3.8 s (a 69 s take).
        self.timeout_s = float(c.get("timeout_s", 4.0))
        self.timeout_per_s = float(c.get("timeout_per_s", 0.1))
        self.key = read_key(c) if self.enabled else ""
        self.terms: list[str] = []
        self.last_error: str | None = None
        self.last_s = 0.0

    def wanted(self, speech_s: float) -> bool:
        return self.enabled and bool(self.key) and speech_s >= self.min_seconds

    def set_terms(self, terms: list[str]) -> None:
        self.terms = [t for t in terms if 0 < len(t) <= TERM_MAX_CHARS][:TERMS_MAX]

    def transcribe(self, audio: np.ndarray, speech_s: float) -> str:
        """The take's text, cleaned. Raises on anything that is not a good answer."""
        self.last_error = None
        t0 = time.perf_counter()
        try:
            data = {"model_id": self.model, "tag_audio_events": "false"}
            if self.terms:
                data["keyterms"] = self.terms   # one form field per term
            r = httpx.post(
                URL, headers={"xi-api-key": self.key}, data=data,
                files={"file": ("take.wav", wav_bytes(audio), "audio/wav")},
                timeout=self.timeout_s + self.timeout_per_s * speech_s)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
            text = clean(str(r.json().get("text") or ""))
            if not text.strip(" ."):
                raise RuntimeError("empty text")
            return text
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            raise
        finally:
            self.last_s = time.perf_counter() - t0
