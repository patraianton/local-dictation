# -*- coding: utf-8 -*-
"""Speech recognition: faster-whisper on the GPU."""
import math
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


SENTENCE_MARKS = ".!?…"


def _with_mark(text: str, mark: str) -> str:
    """The same phrase with its final mark replaced."""
    t = text.rstrip()
    while t and t[-1] in SENTENCE_MARKS:
        t = t[:-1].rstrip()
    return t + mark


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


# Lines the recognizer says when it hears no speech and has to say something.
# They come from the subtitle files it was trained on — the credits at the end
# of a fansubbed video — and they arrive with full confidence, indistinguishable
# from a real take. Found nine times in eighteen days of logs, the worst on
# 25.08.2026: a takeover of a FOUR-MINUTE recording (237.99 s) that came out as
# the three words "Продолжение следует...". The last one was 29.08 at 09:10.
# Matched on letters only, so punctuation and case cannot smuggle one through.
GHOSTS = (
    "субтитры создавал", "субтитры делал", "субтитры сделал",
    "субтитрысоздал", "редактор субтитров", "корректор субтитров",
    "продолжение следует", "подписывайтесь на канал",
    "спасибо за просмотр", "das war ein kleiner test",
)


def subtitle_ghost(text: str) -> str:
    """The stock phrase this take turned out to be, or "" if it is real speech.

    Only whole takes count. A ghost never comes with anything else around it —
    the recognizer either heard speech or filled the silence — so a take that
    merely mentions subtitles ("сделай субтитры к ролику") is left alone.
    """
    plain = PUNCT_RE.sub(" ", (text or "").lower())
    plain = " ".join(plain.split())
    if not plain:
        return ""
    for ghost in GHOSTS:
        if plain == ghost or plain.startswith(ghost + " ") and len(plain) <= len(ghost) + 24:
            return ghost
    return ""


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
        # A second, heavier model for SHORT takes only. A one or two second
        # command is where turbo breaks: there is almost no context to lean on,
        # and it guesses. Measured over 258 short takes from 05-10.09.2026, on
        # the 115 where the two models disagreed, twelve blind judges preferred
        # large-v3 in 32 takes against turbo's 14 — and the ones it fixed are
        # exactly the ones that had to be re-dictated: "Чиньёба цикета" ->
        # "Чинь оба тикета",
        # "Аксим" -> "Максим", "Версара" -> "Vercel", "лавиш" -> "Lavish".
        # On long takes it is the other way round, and four times slower, so
        # the switch is by length. "" turns it off.
        self.short_model_name = str(a.get("short_model", "") or "")
        self.short_seconds = float(a.get("short_seconds", 3.0))
        self.model = None
        self.short = None
        self.last_model = self.model_name   # which model did the last take
        self._tok = None                    # built on first use, see question_score

    def _open_model(self, name: str):
        """Loads one model. The disk copy first, the network never if avoidable."""
        for local_only in (True, False):
            try:
                return WhisperModel(
                    name, device=self.device, compute_type=self.compute_type,
                    local_files_only=local_only,
                )
            except Exception:
                continue
        return None

    def load(self) -> float:
        t0 = time.perf_counter()
        self.model = self._open_model(self.model_name)
        if self.model is None:
            # No GPU available — run on the CPU rather than not run at all.
            self.device, self.compute_type = "cpu", "int8"
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            return time.perf_counter() - t0
        if self.short_model_name and self.short_model_name != self.model_name:
            # Best effort: a second model is worth 3 GB of video memory, but not
            # worth refusing to start over. On the processor it would take
            # seconds per take, so there it is skipped.
            if self.device != "cpu":
                self.short = self._open_model(self.short_model_name)
        return time.perf_counter() - t0

    def pick(self, audio: np.ndarray, speech_s: float | None = None):
        """Which model this take goes to, and its name for the log.

        The length that decides is the SPEECH, not the file: every recording
        also carries half a second of pre-roll and up to 0.4 s of tail, and
        counting those would push a two-and-a-half-second phrase over a
        three-second threshold. That is exactly what happened to "Заводите
        китос, конечно" on 10.09.2026.
        """
        secs = len(audio) / 16000.0 if speech_s is None else speech_s
        if self.short is not None and secs <= self.short_seconds:
            return self.short, self.short_model_name
        return self.model, self.model_name

    def drop_short(self) -> bool:
        """Gives up the second model to free video memory.

        The card is shared with LM Studio, and the model sitting there is the
        owner's, not ours: he may swap a 3 GB corrector for a 30 GB one at any
        moment. On 26.08.2026 exactly that killed dictation — the recognizer
        lost the card and the process quietly died. So when memory runs out,
        the first thing to go is the extra model: short takes get worse, which
        is far better than dictation stopping.
        """
        if self.short is None:
            return False
        self.short = None
        return True

    def warmup(self) -> float:
        """The first run is always slow — get it over with, on silence."""
        t0 = time.perf_counter()
        self.transcribe(np.zeros(16000, dtype=np.float32))
        if self.short is not None:
            # A second of silence goes to the short-take model by length, so
            # this warms that one; a longer buffer warms the main one.
            self.transcribe(np.zeros(int(16000 * (self.short_seconds + 1)),
                                     dtype=np.float32))
        # Same for the weighing path: building the tokenizer costs 140 ms, and
        # without this the first take of the day would pay it.
        self.question_score(np.zeros(16000, dtype=np.float32), "Проверка связи.")
        return time.perf_counter() - t0

    def _run(self, audio: np.ndarray, prompt: str | None, model=None) -> str:
        segments, _info = (model or self.model).transcribe(
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
        self.short = None
        self._tok = None
        try:
            self.load()
            return self.device
        except Exception:
            self.device, self.compute_type = "cpu", "int8"
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            return "cpu"

    def transcribe(self, audio: np.ndarray,
                   speech_s: float | None = None) -> tuple[str, float]:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        t0 = time.perf_counter()
        model, name = self.pick(audio, speech_s)
        self.last_model = name
        text = self._run(audio, self.prompt, model)
        # Looped: almost always the hint is to blame. Retry without it.
        if looped(text):
            text = self._run(audio, None, model)
        return text, time.perf_counter() - t0

    # The longest take that can be weighed for a question. The recognizer reads
    # the sound in one 30-second window; a longer take would have its ending —
    # the only part that matters here — cut off.
    MAX_SCORE_SECONDS = 29.0

    def _tokenizer(self):
        from faster_whisper.tokenizer import Tokenizer

        if self._tok is None:
            self._tok = Tokenizer(
                self.model.hf_tokenizer, self.model.model.is_multilingual,
                task="transcribe", language=self.language,
            )
        return self._tok

    def question_score(self, audio: np.ndarray, text: str) -> float | None:
        """How much more the sound looks like a question than like a statement.

        The recognizer writes the final mark itself, in one pass, and whatever
        it decided is what stays — a question said without a question word
        ("Мы ссылки сделали на эти статьи?") comes out as a statement. But it
        can be asked differently: give it the SAME phrase twice, once ending in
        "." and once in "?", and make it read both against the recording. It
        returns how well each version agrees with the sound. The difference is
        the answer to "did the voice go up at the end".

        This is not pitch measured by hand (that was tried, bench/pitch.py, and
        it is weak): it is the same network that already hears intonation,
        simply asked directly.

        Returns log P(with "?") - log P(with "."). Above zero means the sound
        looks like a question. None when the take cannot be weighed (too long,
        no text, the model is busy elsewhere) — never raises: a take must be
        pasted even if this fails.

        Measured over 421 real takes on 08-10.09.2026, with the threshold at
        -1.0: five questions saved over three days and not one extra mark. See
        bench/qscore.py and bench/qpolicy.py.
        """
        if self.model is None or not text.strip():
            return None
        if len(audio) / 16000.0 > self.MAX_SCORE_SECONDS:
            return None
        try:
            from faster_whisper.audio import pad_or_trim

            tok = self._tokenizer()
            features = self.model.feature_extractor(audio)
            n_frames = features.shape[-1]
            enc = self.model.encode(pad_or_trim(features))
            start = list(tok.sot_sequence)

            def logprob(variant: str) -> float:
                tokens = tok.encode(" " + variant.strip())
                res = self.model.model.align(
                    enc, start, [tokens], n_frames, median_filter_width=7
                )[0]
                return sum(math.log(max(p, 1e-9)) for p in res.text_token_probs)

            return logprob(_with_mark(text, "?")) - logprob(_with_mark(text, "."))
        except Exception:
            return None
