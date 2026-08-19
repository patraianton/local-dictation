# -*- coding: utf-8 -*-
"""Распознавание речи: faster-whisper на видеокарте."""
import re
import time

import numpy as np

from . import cuda_fix

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def build_prompt(terms: list[str], limit: int, style: str = "sample") -> str:
    """Подсказка распознавалке.

    Она делает две вещи сразу: учит модель твоим словам И задаёт стиль записи —
    с заглавными буквами и знаками препинания. Без подсказки текст выходит
    сплошной строчной кашей.
    """
    picked = [t for t in terms[:limit] if t]
    if not picked:
        return ""
    if style == "commands":
        # Приказы в подсказке — против самой вредной ошибки: распознавалка слышит
        # «сделай» как «сделаю» и переворачивает смысл на противоположный.
        # Замерено на 383 записях: без приказов 4 потерянных приказа при 17,0%
        # ошибок, с этими глаголами 2 при 17,4%. Вариант с целыми фразами давал
        # 1 потерянный, но 18,2% — содержательные слова из подсказки начинали
        # всплывать в тексте. Поэтому здесь только глаголы, без существительных.
        return (
            "Сделай, проверь, посмотри, запусти, запускай, отправляй, продолжай, "
            "отгружай, собери, поставь, обнови, покажи, найди, добавь, открой. "
            "Термины и названия: " + ", ".join(picked) + "."
        )
    if style == "list":
        return "Термины и названия: " + ", ".join(picked) + "."
    # Живая фраза в его стиле работает лучше сухого списка: модель копирует
    # не только слова, но и оформление.
    return (
        "Окей, смотри: закинь этот worktree в Claude Code, поставь loop на пять часов, "
        "потом глянь Intercom и Mailflow, и обнови дашборд в PostHog. "
        "По signups и MRR за неделю дай отдельную табличку. "
        "Термины: " + ", ".join(picked) + "."
    )


def looped(text: str, times: int = 4) -> bool:
    """Признак срыва в повтор: одна тройка слов повторяется снова и снова.

    Настоящий сбой распознавалки — она уходит в «Харьков, Мори, Харьков, Мори…»
    и выдаёт мусор на пол-экрана.
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
        # Сначала пробуем взять модель с диска и вообще не ходить в сеть:
        # так запуск не зависит от интернета и не ждёт проверки версии.
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
        # Видеокарта недоступна — работаем на процессоре, лишь бы не молчать.
        self.device, self.compute_type = "cpu", "int8"
        self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return time.perf_counter() - t0

    def warmup(self) -> float:
        """Первый прогон всегда медленный — делаем его заранее, на тишине."""
        t0 = time.perf_counter()
        self.transcribe(np.zeros(16000, dtype=np.float32))
        return time.perf_counter() - t0

    def _run(self, audio: np.ndarray, prompt: str | None) -> str:
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            # Список температур = право отступить: если вышла бессмыслица
            # (слишком сжатый или невероятный текст), модель переспрашивает сама.
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

    # Признаки того, что видеокарта отвалилась и модель в памяти больше не жива.
    # Случается после сна компьютера: 19.08.2026 программа так провисела четыре
    # дня, отвечая «СБОЙ» на каждую диктовку, пока её не перезапустили руками.
    LOST_GPU = ("cuda", "cudnn", "cublas", "gpu", "device-side", "out of memory")

    @classmethod
    def looks_like_lost_gpu(cls, exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(mark in text for mark in cls.LOST_GPU)

    def reload(self) -> str:
        """Поднимает модель заново после потери видеокарты.

        Сначала пробуем ту же видеокарту — обычно контекст просто надо создать
        заново. Не вышло — переходим на процессор: медленнее, но диктовка живая.
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
            raise RuntimeError("модель не загружена")
        t0 = time.perf_counter()
        text = self._run(audio, self.prompt)
        # Сорвалась в повтор — почти всегда виновата подсказка. Пробуем без неё.
        if looped(text):
            text = self._run(audio, None)
        return text, time.perf_counter() - t0
