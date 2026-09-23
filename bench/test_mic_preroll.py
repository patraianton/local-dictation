# -*- coding: utf-8 -*-
"""Микрофон держится открытым, и начало фразы больше не теряется.

23.08.2026 выяснилось: программа открывала микрофон только после нажатия
клавиши, а это 66-105 миллисекунд. От 26 до 42 процентов надиктовок начинались
с речи в первом же кадре, то есть первый слог не записывался вовсе («Читайся»
вместо «Отчитайся»). Теперь микрофон остаётся открытым после надиктовки, и
полсекунды звука ДО нажатия клавиши уже лежат в памяти.

Настоящего микрофона здесь нет — звуковая карта подделана.

    ..\\.venv\\Scripts\\python.exe test_mic_preroll.py
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import audio as A  # noqa: E402

SR = 48000
BLOCK = 480          # 10 мс


class FakeStream:
    """Подделка звукового потока: шлёт блоки по расписанию, как настоящая."""

    open_count = 0
    alive = 0

    def __init__(self, samplerate, channels, dtype, device, callback, blocksize):
        FakeStream.open_count += 1
        self.sr = samplerate
        self.cb = callback
        self.value = 0.0          # что «слышно» прямо сейчас
        self._stop = threading.Event()
        self._t = None

    def start(self):
        FakeStream.alive += 1

        def loop():
            # Звук идёт ровно по часам, а не по тому, как ОС раздала время:
            # иначе на загруженной машине тест врёт про длину предзаписи.
            t0, sent = time.perf_counter(), 0
            while not self._stop.wait(0.005):
                want = int((time.perf_counter() - t0) * SR)
                while sent < want:
                    n = min(BLOCK, want - sent)
                    self.cb(np.full((n,), self.value, dtype=np.float32),
                            n, None, None)
                    sent += n

        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()

    def close(self):
        FakeStream.alive -= 1


def make(preroll_s=0.5, hot_s=5.0):
    rec = A.Recorder([7], SR, preroll_s=preroll_s, hot_s=hot_s)
    rec.recipe = (7, SR, 1)      # чтобы не спрашивать настоящую карту
    return rec


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def main():
    A.sd.InputStream = FakeStream
    ok = []

    # --- 1. Предзапись: звук ДО нажатия клавиши попадает в надиктовку ---
    rec = make()
    rec.warm()
    rec._stream.value = 0.25          # «фон» до нажатия
    time.sleep(0.9)                   # заведомо больше, чем полсекунды
    rec.start()
    pre = rec._pending_preroll
    rec._stream.value = 0.75          # «речь» после нажатия
    time.sleep(0.5)
    data = rec.stop()
    if not 0.4 <= pre <= 0.6:
        fail(f"предзапись не полсекунды, а {pre:.2f} с")
    n_pre = int(rec.last_preroll_s * A.TARGET_SR)
    # На стыке пересчёт частоты слегка звенит — сравниваем по середине куска.
    head = float(np.median(data[20 : n_pre - 40]))
    tail = float(np.median(data[n_pre + 800:]))
    if abs(head - 0.25) > 0.02:
        fail(f"в начале надиктовки не тот звук, что был до нажатия: {head:.3f}")
    if abs(tail - 0.75) > 0.02:
        fail(f"после нажатия записалось не то: {tail:.3f}")
    ok.append(f"предзапись {rec.last_preroll_s*1000:.0f} мс попала в надиктовку")

    # --- 2. Предзапись не растёт бесконечно, пока никто не диктует ---
    time.sleep(1.0)                   # долгое молчание
    held = rec._held / SR
    if held > 0.7:
        fail(f"в памяти скопилось {held:.2f} с вместо полсекунды")
    ok.append(f"в простое держится {held*1000:.0f} мс, память не растёт")

    # --- 3. Микрофон отпускается сам, когда им долго не пользуются ---
    rec.hot_s = 0.3
    rec.start(); time.sleep(0.05); rec.stop()
    time.sleep(1.0)
    if rec._stream is not None:
        fail("микрофон не отпущен, другие программы не смогут писать")
    ok.append("микрофон отпущен по времени")

    # --- 4. ...и сразу же, если человек ушёл в другое окно ---
    rec.hot_s = 30.0
    rec.should_hold = lambda: False
    rec.start(); time.sleep(0.05); rec.stop()
    time.sleep(0.6)
    if rec._stream is not None:
        fail("при переходе в другое окно микрофон не отпущен")
    ok.append("микрофон отпущен при уходе в другое окно")

    # --- 5. Ничего не течёт: сколько открыли, столько и закрыли ---
    rec.should_hold = None
    rec.close()
    time.sleep(0.1)
    if FakeStream.alive != 0:
        fail(f"остались открытые потоки: {FakeStream.alive}")
    ok.append(f"открывали {FakeStream.open_count} раз, всё закрыто")

    # --- 6. Без удержания (hot_ms = 0) работает по-старому ---
    FakeStream.open_count = 0
    rec = make(preroll_s=0.5, hot_s=0.0)
    if rec.warm():
        fail("при hot_ms = 0 микрофон не должен держаться открытым")
    rec.start(); time.sleep(0.1)
    data = rec.stop()
    if rec.last_preroll_s > 0.01:
        fail("без удержания предзаписи взяться неоткуда")
    if data.size == 0:
        fail("без удержания надиктовка вообще не записалась")
    if rec._stream is not None:
        fail("без удержания микрофон должен закрываться сразу")
    ok.append("режим без удержания работает как раньше")

    for line in ok:
        print(f"[v] {line}")
    print("\nвсё сошлось")


if __name__ == "__main__":
    main()
