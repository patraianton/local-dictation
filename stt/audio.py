# -*- coding: utf-8 -*-
"""Запись с микрофона."""
import threading
import time

import numpy as np
import sounddevice as sd

TARGET_SR = 16000


def list_inputs() -> list[dict]:
    """Все устройства записи с именем хост-подсистемы."""
    apis = sd.query_hostapis()
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            out.append(
                {
                    "index": idx,
                    "name": dev["name"],
                    "hostapi": apis[dev["hostapi"]]["name"],
                    "default_samplerate": int(dev["default_samplerate"]),
                    "channels": dev["max_input_channels"],
                }
            )
    return out


# Один и тот же микрофон Windows показывает несколько раз — через разные
# подсистемы, и они принимают разные форматы. У RØDE PodMic: MME берёт 16 кГц
# моно как есть, а WASAPI требует 48 кГц стерео и всё равно срывается на старте.
# Поэтому порядок именно такой, и мы перебираем, пока что-то не откроется.
HOSTAPI_ORDER = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")


def find_devices(name_hint: str) -> list:
    """Все подходящие входы по куску названия, от самого надёжного к остальным.

    Последним всегда идёт None — микрофон Windows по умолчанию.
    """
    if not name_hint:
        return [None]
    hint = name_hint.strip().lower()
    matches = [d for d in list_inputs() if hint in d["name"].lower()]
    if not matches:
        return [None]
    order = {name: i for i, name in enumerate(HOSTAPI_ORDER)}
    matches.sort(key=lambda d: order.get(d["hostapi"], 99))
    return [d["index"] for d in matches] + [None]


def find_device(name_hint: str):
    """Первый подходящий вход — для показа в проверках и журнале."""
    return find_devices(name_hint)[0]


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return audio
    try:
        import soxr

        return soxr.resample(audio, src_sr, dst_sr).astype(np.float32)
    except ImportError:
        # Запасной вариант: усреднение соседних отсчётов вместо фильтра.
        # Хуже по качеству, но лучше, чем ничего.
        ratio = src_sr / dst_sr
        n = int(len(audio) / ratio)
        idx = (np.arange(n) * ratio).astype(np.int64)
        return audio[idx].astype(np.float32)


class Recorder:
    """Пишет моно-звук в память, пока идёт запись."""

    def __init__(self, devices=None, samplerate: int = TARGET_SR):
        # Принимаем и один вход, и список — чтобы было что перебирать.
        if devices is None or isinstance(devices, int):
            devices = [devices]
        self.devices = list(devices)
        self.want_sr = samplerate
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._open_sr = samplerate
        self._open_ch = 1
        self.recipe = None  # что сработало — дальше открываем сразу этим
        self.last_error = ""

    @property
    def device(self):
        return self.recipe[0] if self.recipe else self.devices[0]

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        with self._lock:
            block = indata if indata.ndim == 1 else indata.mean(axis=1)
            self._chunks.append(np.asarray(block, dtype=np.float32).copy())

    def _recipes(self):
        """Способы открыть микрофон, от самого желанного к любому рабочему."""
        if self.recipe:
            yield self.recipe
            return
        for dev in self.devices:
            try:
                info = sd.query_devices(dev, "input")
                own_sr = int(info["default_samplerate"])
                max_ch = int(info["max_input_channels"]) or 1
            except Exception:
                own_sr, max_ch = 48000, 2
            seen = set()
            for sr in (self.want_sr, own_sr, 48000, 44100):
                for ch in (1, max_ch):
                    if (sr, ch) in seen or ch < 1:
                        continue
                    seen.add((sr, ch))
                    yield (dev, sr, ch)

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
        errors = []
        for dev, sr, ch in self._recipes():
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=sr, channels=ch, dtype="float32",
                    device=dev, callback=self._callback, blocksize=0,
                )
                stream.start()
            except Exception as exc:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                errors.append(f"{dev}/{sr}Гц/{ch}кан: {str(exc)[:60]}")
                continue
            self._stream = stream
            self._open_sr, self._open_ch = sr, ch
            self.recipe = (dev, sr, ch)
            return
        self.last_error = " | ".join(errors[:3])
        raise RuntimeError(f"не удалось открыть микрофон: {self.last_error}")

    def _samples(self) -> int:
        with self._lock:
            return sum(len(c) for c in self._chunks)

    def _last_samples(self, n: int) -> np.ndarray:
        """Последние n отсчётов из уже записанного."""
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        with self._lock:
            chunks = list(self._chunks)
        take, total = [], 0
        for c in reversed(chunks):
            take.append(c)
            total += len(c)
            if total >= n:
                break
        if not take:
            return np.zeros(0, dtype=np.float32)
        a = np.concatenate(list(reversed(take)))
        return a[-n:] if n < len(a) else a

    @staticmethod
    def _rms(a: np.ndarray) -> float:
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0

    def _wait_tail(self, max_tail_s: float, quiet_ms: int, rel: float) -> None:
        """Дописывает хвост после отпускания клавиши.

        Человек отпускает клавишу, ещё договаривая последнее слово — и оно
        пропадало. Замерено 15.08.2026: у 47% записей в конце не было ни одного
        тихого кадра, то есть звук резался на полуслове.

        Ждём не всегда до упора: как только пошла тишина — останавливаемся.
        Если он отпустил уже в паузе, задержки почти нет.
        """
        if max_tail_s <= 0:
            return
        # За «громкость речи» берём последнюю секунду записи.
        base = self._rms(self._last_samples(self._open_sr))
        thr = max(base * rel, 0.004)
        step = 0.02
        need = quiet_ms / 1000.0
        quiet_for = 0.0
        deadline = time.perf_counter() + max_tail_s
        mark = self._samples()
        while time.perf_counter() < deadline:
            time.sleep(step)
            now = self._samples()
            new = now - mark
            mark = now
            if new <= 0 or self._rms(self._last_samples(new)) <= thr:
                quiet_for += step
            else:
                quiet_for = 0.0
            if quiet_for >= need:
                return

    def stop(self, tail_s: float = 0.0, quiet_ms: int = 120,
             quiet_rel: float = 0.12) -> np.ndarray:
        """Останавливает запись и отдаёт звук 16 кГц моно.

        tail_s — сколько максимум дописывать после команды «стоп».
        """
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        try:
            self._wait_tail(tail_s, quiet_ms, quiet_rel)
        except Exception:
            pass
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32)
        return _resample(audio, self._open_sr, TARGET_SR)

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def seconds(self) -> float:
        with self._lock:
            n = sum(len(c) for c in self._chunks)
        return n / float(self._open_sr or TARGET_SR)


def loudness(audio: np.ndarray) -> tuple[float, float]:
    """(пик, среднеквадратичная громкость) — чтобы понять, что микрофон молчит."""
    if audio.size == 0:
        return 0.0, 0.0
    return float(np.abs(audio).max()), float(np.sqrt(np.mean(audio**2)))


def normalize(audio: np.ndarray) -> np.ndarray:
    """Тихую запись подтягиваем по громкости, громкую не трогаем."""
    peak, _ = loudness(audio)
    if 0.0 < peak < 0.25:
        return (audio * (0.5 / peak)).astype(np.float32)
    return audio
