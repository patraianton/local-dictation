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
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np

URL = "https://api.elevenlabs.io/v1/speech-to-text"
# The service refuses a term of 50 characters or more (measured 30.10.2026).
TERM_MAX_CHARS = 49
TERMS_MAX = 100

# "а-а-а", "э-э-э", "э", "м-м", "эм", "мм", "uh", "um" — a sound, not a word.
FILLER_RE = re.compile(
    r"(?<!\w)(?:[аэмуеои](?:-[аэмуеои])+|э+м*|м{2,}|uh|um|hmm|mm)(?!\w)[,.]?\s*", re.I)
# A letter drawn out three times or more at the end of a word: "емууу" -> "ему",
# "Иии" -> "И" (the first one a capital: hence any case).
STRETCH_RE = re.compile(r"(\w)\1{2,}\b", re.I)
# A word broken off with a double dash: "простой--" -> "простой".
BROKEN_RE = re.compile(r"(\w)--(?=\s|$)")
# Stammering, written down as it was said (01.11.2026, the first day live): the
# start of a word said three times and more, "по-по-по-покрасить"; a word said
# three times and more with hyphens, "как-как-как" (twice is Russian: "чуть-чуть");
# a word again after a comma, three times and more, or twice for a word of one or
# two letters, "Мы, мы, мы пошли", "в, в лес"; a dash standing alone, "типа, -.".
# 02.11: the stammered syllable need not be the word's own start, only its first
# letter ("ку-ку-кофе"), and two words can come twice ("как в, как в лес").
STAMMER_START_RE = re.compile(r"\b((\w)\w{0,3})(?:-\1)+-(?=\2\w)", re.I)
# A single first letter said ahead of the word: "с-сразу", "о-очень"; or the
# word's first syllable said once ahead of it, "ка-как" — but never "по-",
# which is Russian itself ("по-польски", "по-полной"), nor "так-таки".
# 03.11: "по-пофикси" is a stammer too; the Russian ones go on with "пол", "пор",
# "под", "пох" ("по-польски", "по-португальски", "по-подлому", "по-походному").
STAMMER_LETTER_RE = re.compile(r"\b(?!по-по[лрдх]|так-)(\w{1,3})-(?=\1\w)", re.I)
STAMMER_WORD_RE = re.compile(r"\b(\w+)(?:-\1\b){2,}", re.I)
# A hyphened start said twice: "по-по-твоему" -> "по-твоему".
STAMMER_DUP_RE = re.compile(r"\b(\w{1,3})-(?=\1-\w)", re.I)
# 03.11: a word of three letters too ("как, как будто"), but no number ("10, 10").
REPEAT_RE = re.compile(r"\b(\w+)(?:,\s+\1\b){2,}|\b([^\W\d_]{1,3})(?:,\s+\2\b)+", re.I)
REPEAT_PAIR_RE = re.compile(r"\b(\w+\s+\w+)(?:,\s+\1\b)+", re.I)
# A short word said twice with no comma between: "про про эти", "на на".
REPEAT_SHORT_RE = re.compile(r"\b([^\W\d_]{1,3})(?:\s+\1\b)+", re.I)
LONE_DASH_RE = re.compile(r"\s+[-—]+(?=\s|[,.!?…]|$)")
# A phrase broken off on a half word and said again whole: "можешь пис можешь
# писать" -> "можешь писать" (the half word is the start of the later one).
RESTART_RE = re.compile(r"\b((?:\w+\s+){1,5})(\w{1,6})\s+\1(?=\2\w)", re.I)


def _first(m: re.Match) -> str:
    return m.group(1) or m.group(2)


def clean(text: str) -> str:
    """The cloud's verbatim text, the way a take read here comes out."""
    t = FILLER_RE.sub("", text)
    t = STRETCH_RE.sub(r"\1", t)
    t = BROKEN_RE.sub(r"\1", t)
    t = STAMMER_START_RE.sub("", t)
    t = STAMMER_LETTER_RE.sub("", t)
    t = STAMMER_WORD_RE.sub(r"\1", t)
    t = STAMMER_DUP_RE.sub("", t)
    t = REPEAT_RE.sub(_first, t)
    t = REPEAT_PAIR_RE.sub(r"\1", t)
    t = REPEAT_SHORT_RE.sub(r"\1", t)
    t = RESTART_RE.sub(r"\1", t)
    t = LONE_DASH_RE.sub("", t)
    t = re.sub(r"\s+([,.!?…])", r"\1", re.sub(r"\s{2,}", " ", t))
    t = re.sub(r",(?=\s*[,.!?…])", "", t).strip(" ,")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    # A sentence whose capital went with a stammer or a filler ("О-очень", "А-а, так").
    t = re.sub(r"([.!?…]\s+)([a-zа-яё])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t and t[-1] not in ".!?…":
        t += "."
    return t


def _post_within(limit: float, url: str, **kw) -> httpx.Response:
    """The request, given up after `limit` seconds in all.

    httpx's own timeout counts each step on its own — connecting, sending,
    every wait for the next piece of the answer — so a slow answer went on for
    13 s against a 4.6 s limit (03.11.2026). The request runs in a thread here;
    when the time is up it is left to finish on its own and its answer is not
    waited for.
    """
    box: dict = {}

    def run() -> None:
        try:
            box["r"] = httpx.post(url, timeout=limit, **kw)
        except Exception as exc:
            box["e"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit)
    if "r" in box:
        return box["r"]
    if "e" in box:
        raise box["e"]
    raise TimeoutError(f"no answer within {limit:.1f} s")


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
        # The take is read here meanwhile; the cloud is waited for this long past
        # that reading, and dropped if later (__main__._cloud_and_here).
        self.grace_s = float(c.get("grace_s", 1.0))
        # ...but a long take may take the cloud its usual time: up to grace_s +
        # grace_per_s per second of speech from the start, whichever is later.
        self.grace_per_s = float(c.get("grace_per_s", 0.0))
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
            limit = self.timeout_s + self.timeout_per_s * speech_s
            r = _post_within(limit, URL, headers={"xi-api-key": self.key}, data=data,
                             files={"file": ("take.wav", wav_bytes(audio), "audio/wav")})
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
