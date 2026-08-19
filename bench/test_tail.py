# -*- coding: utf-8 -*-
"""Проверка дозаписи хвоста: последнее слово не должно теряться.

    ..\\.venv\\Scripts\\python.exe test_tail.py

Настоящий микрофон не нужен: поток подделан, звук подаётся вручную.
Замер 15.08.2026: 47% записей обрывались на звуке — человек отпускает клавишу,
ещё договаривая слово.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.audio import Recorder  # noqa: E402

SR = 16000


class FakeStream:
    """Поток, который якобы открыт."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def close(self):
        pass


def feeder(rec: Recorder, plan, stop_flag):
    """Подаёт звук кусочками по 20 мс по расписанию: (сколько мс, громкость)."""
    for ms, level in plan:
        for _ in range(max(1, ms // 20)):
            if stop_flag[0]:
                return
            n = SR // 50                       # 20 мс
            block = (np.random.default_rng(0).standard_normal(n) * level
                     ).astype(np.float32)
            with rec._lock:
                rec._chunks.append(block)
            time.sleep(0.02)


def run(plan, tail_ms, quiet_ms=120):
    """Возвращает (сколько записалось секунд, сколько ждали секунд)."""
    rec = Recorder(devices=[None], samplerate=SR)
    rec._stream = FakeStream()
    rec._open_sr, rec._open_ch = SR, 1
    stop_flag = [False]
    th = threading.Thread(target=feeder, args=(rec, plan, stop_flag), daemon=True)
    th.start()
    time.sleep(0.25)                            # дали немного записаться
    t0 = time.perf_counter()
    audio = rec.stop(tail_s=tail_ms / 1000.0, quiet_ms=quiet_ms)
    waited = time.perf_counter() - t0
    stop_flag[0] = True
    return len(audio) / SR, waited


def main() -> None:
    bad = 0

    def check(ok, why, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok and detail:
            print(f"      {detail}")

    # 1. человек ещё говорит — ждём почти весь отведённый хвост
    _sec, waited = run([(3000, 0.2)], tail_ms=400)
    check(0.30 <= waited <= 0.55,
          "говорит на момент отпускания — дописываем хвост",
          f"ждали {waited*1000:.0f} мс, ожидали около 400")

    # 2. человек уже замолчал — почти не ждём
    _sec, waited = run([(200, 0.2), (3000, 0.0005)], tail_ms=400)
    check(waited <= 0.25,
          "отпустил в паузе — задержки почти нет",
          f"ждали {waited*1000:.0f} мс, ожидали не больше 250")

    # 3. хвост записался: звука стало больше, чем было на момент «стоп»
    plan = [(3000, 0.2)]
    rec = Recorder(devices=[None], samplerate=SR)
    rec._stream = FakeStream()
    rec._open_sr, rec._open_ch = SR, 1
    flag = [False]
    threading.Thread(target=feeder, args=(rec, plan, flag), daemon=True).start()
    time.sleep(0.30)
    before = rec._samples()
    audio = rec.stop(tail_s=400 / 1000.0)
    flag[0] = True
    check(len(audio) > before + SR * 0.2,
          "в записи прибавилось звука после отпускания",
          f"было {before} отсчётов, стало {len(audio)}")

    # 4. хвост выключен — останавливаемся сразу
    _sec, waited = run([(3000, 0.2)], tail_ms=0)
    check(waited < 0.08, "с выключенным хвостом останавливается мгновенно",
          f"ждали {waited*1000:.0f} мс")

    # 5. поток не открыт — не падаем
    rec = Recorder(devices=[None], samplerate=SR)
    out = rec.stop(tail_s=0.4)
    check(out.size == 0, "остановка без записи ничего не ломает")

    print(f"\n{'всё сошлось' if not bad else str(bad) + ' не сошлось'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
