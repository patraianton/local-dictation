# -*- coding: utf-8 -*-
"""Speech recognition: faster-whisper on the GPU."""
import re
import time

import numpy as np

from . import cuda_fix

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def build_prompt(terms: list[str], limit: int, style: str = "sample") -> str:
    """The hint given to the recognizer.

    It does two things at once: teaches the model your words AND sets the
    writing style — capitals and punctuation. Without a hint the output is one
    long lowercase blur.
    """
    picked = [t for t in terms[:limit] if t]
    if not picked:
        return ""
    if style == "commands":
        # Imperative verbs in the hint fight the most damaging error there is:
        # the recognizer hears "сделай" (do it) as "сделаю" (I will do it) and
        # inverts the meaning. Measured on 383 takes: without them, 4 orders
        # lost at 17.0% word error; with these verbs, 2 lost at 17.4%. A variant
        # using whole sentences lost only 1 but scored 18.2% — content words
        # from the hint started leaking into the transcript. Hence verbs only,
        # no nouns.
        return (
            "Сделай, проверь, посмотри, запусти, запускай, отправляй, продолжай, "
            "отгружай, собери, поставь, обнови, покажи, найди, добавь, открой. "
            "Термины и названия: " + ", ".join(picked) + "."
        )
    if style == "list":
        return "Термины и названия: " + ", ".join(picked) + "."
    # A natural sentence in the speaker's own style beats a dry list: the model
    # copies not just the words but the formatting too.
    return (
        "Окей, смотри: закинь этот worktree в Claude Code, поставь loop на пять часов, "
        "потом глянь Intercom и Mailflow, и обнови дашборд в PostHog. "
        "По signups и MRR за неделю дай отдельную табличку. "
        "Термины: " + ", ".join(picked) + "."
    )


def looped(text: str, times: int = 4) -> bool:
    """Detects a decoding loop: the same word triple repeating over and over.

    A real failure mode of the recognizer — it slides into
    "Харьков, Мори, Харьков, Мори..." and fills half the screen with garbage.
    """
    words = PUNCT_RE.sub(" ", text.lower()).split()
    if len(words) < times * 3:
        return False
    seen: dict[tuple, int] = {}
    for i in range(len(words) - 2):
        gram = (words[i], words[i + 1], words[i + 2])
        seen[gram] = seen.get(gram, 0) + 1
        if seen[gram] >= times:
            return True
    return False


class Asr:
    def __init__(self, cfg: dict, terms: list[str]):
        a = cfg["asr"]
        self.language = a.get("language", "ru")
        self.beam_size = int(a.get("beam_size", 1))
        self.vad = bool(a.get("vad", True))
        self.prompt = build_prompt(
            terms, int(a.get("prompt_terms", 45)), a.get("prompt_style", "sample")
        )
        self.device = a.get("device", "cuda")
        self.compute_type = a.get("compute_type", "float16")
        self.model_name = a.get("model", "large-v3-turbo")
        self.model = None

    def load(self) -> float:
        t0 = time.perf_counter()
        # Try the on-disk copy first and stay off the network entirely: that
        # way startup does not depend on the internet or wait for a version
        # check.
        for local_only in (True, False):
            try:
                self.model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    local_files_only=local_only,
                )
                return time.perf_counter() - t0
            except Exception:
                continue
        # No GPU available — run on the CPU rather than not run at all.
        self.device, self.compute_type = "cpu", "int8"
        self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return time.perf_counter() - t0

    def warmup(self) -> float:
        """The first run is always slow — get it over with, on silence."""
        t0 = time.perf_counter()
        self.transcribe(np.zeros(16000, dtype=np.float32))
        return time.perf_counter() - t0

    def _run(self, audio: np.ndarray, prompt: str | None) -> str:
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            # A list of temperatures gives the model a way out: if the
            # result looks like nonsense (over-compressed or improbable text)
            # it retries on its own.
            temperature=[0.0, 0.2, 0.4, 0.6],
            compression_ratio_threshold=2.4,
            repetition_penalty=1.15,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            vad_filter=self.vad,
            vad_parameters={"min_silence_duration_ms": 300},
            without_timestamps=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    # Marks of a GPU that fell away, leaving the in-memory model dead.
    # Happens after the machine sleeps: on 2026-08-19 the app hung like this for
    # four days, answering "FAILED" to every take until restarted by hand.
    LOST_GPU = ("cuda", "cudnn", "cublas", "gpu", "device-side", "out of memory")

    @classmethod
    def looks_like_lost_gpu(cls, exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(mark in text for mark in cls.LOST_GPU)

    def reload(self) -> str:
        """Brings the model back after the GPU was lost.

        Tries the same GPU first — usually the context just needs recreating.
        If that fails, falls back to the CPU: slower, but dictation stays alive.
        """
        self.model = None
        try:
            self.load()
            return self.device
        except Exception:
            self.device, self.compute_type = "cpu", "int8"
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            return "cpu"

    def transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        t0 = time.perf_counter()
        text = self._run(audio, self.prompt)
        # Looped: almost always the hint is to blame. Retry without it.
        if looped(text):
            text = self._run(audio, None)
        return text, time.perf_counter() - t0
