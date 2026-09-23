# -*- coding: utf-8 -*-
"""Диктовка сама держит усиление микрофона и не даёт его увести.

Разбор 23.08.2026. Ползунок «уровень» у RODE PodMic USB — это не громкость в
Windows, а усилитель внутри самого микрофона, от 22 до 63 децибел. Его двигают
чужие программы (Chrome, Zoom, Telegram) и оставляют сдвинутым:

* 21.08 после Chrome он оказался на самом верху, 63 дБ — речь зашкаливала, в
  записях пошёл треск;
* к 23.08 он сполз на 42,8 дБ — речь шла на десятой доле от полной шкалы, паузы
  проваливались в цифровую тишину, распознавалка начала выдумывать слова.

Микрофон был исправен оба раза. Здесь проверяется, что теперь усиление держит
диктовка: настоящего микрофона нет, устройство подделано.

    ..\\.venv\\Scripts\\python.exe test_micgain.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import micgain as M  # noqa: E402


class FakeVol:
    """Подделка усилителя микрофона: помнит децибелы и свои пределы."""

    def __init__(self, db=42.84, lo=22.0, hi=63.0):
        self.db = float(db)
        self.lo, self.hi = lo, hi
        self.writes = 0

    def GetVolumeRange(self):
        return (self.lo, self.hi, 1.0)

    def GetMasterVolumeLevel(self):
        return self.db

    def SetMasterVolumeLevel(self, value, _ctx):
        self.db = float(value)
        self.writes += 1


def make(tmp, db=42.84, must_open=True, **over):
    cfg = {"mic": {"keep_gain": True, "target_peak": 0.5,
                   "min_db": 34.0, "max_db": 58.0, "max_step_db": 6.0}}
    cfg["mic"].update(over)
    vol = FakeVol(db)
    M._endpoint = lambda hint: (vol, "Desktop Microphone (RODE PodMic USB)")
    g = M.MicGain("PodMic", cfg, Path(tmp) / "micgain.json", lambda m: None)
    opened = g.open()
    if must_open and not opened:
        fail("устройство должно найтись")
    return g, vol


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def main():
    ok = []
    tmp = tempfile.mkdtemp()

    # --- 1. Тихая надиктовка поднимает усиление, громкая опускает ---
    g, vol = make(tmp)
    g.adapt(peak=0.13, secs=4.0)          # ровно то, что было 23.08
    if vol.db <= 42.9:
        fail(f"после тихой надиктовки усиление не подняли: {vol.db:.1f} дБ")
    up = vol.db
    g.adapt(peak=0.999, secs=4.0)         # ровно то, что было 21.08
    if vol.db >= up:
        fail(f"после зашкала усиление не опустили: {vol.db:.1f} дБ")
    ok.append(f"тихо -> {up:.1f} дБ, зашкал -> {vol.db:.1f} дБ")

    # --- 2. Нормальная надиктовка ничего не трогает ---
    g, vol = make(tmp)
    before, writes = vol.db, vol.writes
    for peak in (0.35, 0.5, 0.72):
        g.adapt(peak=peak, secs=3.0)
    if vol.db != before or vol.writes != writes:
        fail("усиление дёргается на нормальных надиктовках")
    ok.append("на нормальном уровне усиление не трогается")

    # --- 3. За свои пределы не выходит никогда ---
    g, vol = make(tmp, db=57.0)
    for _ in range(10):
        g.adapt(peak=0.001, secs=3.0)     # «всегда тихо» — тянет вверх без конца
    if vol.db > 58.0:
        fail(f"вылезли выше потолка: {vol.db:.1f} дБ")
    top = vol.db
    for _ in range(10):
        g.adapt(peak=1.0, secs=3.0)       # «всегда зашкал» — тянет вниз
    if vol.db < 34.0:
        fail(f"провалились ниже пола: {vol.db:.1f} дБ")
    ok.append(f"держится в пределах 34..58 (дошло до {top:.1f} и {vol.db:.1f})")

    # --- 4. Тишина и случайные нажатия ничего не решают ---
    g, vol = make(tmp)
    before = vol.db
    g.adapt(peak=0.0002, secs=5.0)        # микрофон молчал
    g.adapt(peak=0.02, secs=0.2)          # палец соскользнул
    if vol.db != before:
        fail("усиление подстроилось под тишину или под случайное нажатие")
    ok.append("тишина и случайное нажатие усиление не меняют")

    # --- 5. Чужая программа увела ползунок — сторож возвращает ---
    g, vol = make(tmp)
    g.want_db = 48.0
    g.start(period_s=0.05)
    vol.db = 63.0                          # «Chrome выкрутил на максимум»
    time.sleep(0.4)
    g.stop()
    if abs(vol.db - 48.0) > 0.1:
        fail(f"сторож не вернул усиление: {vol.db:.1f} дБ")
    ok.append("чужой сдвиг усиления возвращается назад за доли секунды")

    # --- 6. Выбранный уровень переживает перезапуск ---
    g, vol = make(tmp)
    g.adapt(peak=0.1, secs=4.0)
    chosen = g.want_db
    saved = json.loads((Path(tmp) / "micgain.json").read_text(encoding="utf-8"))
    if abs(saved["db"] - chosen) > 0.01:
        fail("уровень не записан на диск")
    g2, vol2 = make(tmp, db=22.0)          # как будто кто-то всё убил до нуля
    if abs(g2.want_db - chosen) > 0.01:
        fail(f"после перезапуска взяли не свой уровень: {g2.want_db:.1f}")
    ok.append(f"уровень {chosen:.1f} дБ пережил перезапуск")

    # --- 7. Выключенная настройка не трогает ничего ---
    g, vol = make(tmp, must_open=False, keep_gain=False)
    before = vol.db
    g.adapt(peak=0.01, secs=5.0)
    g.start(period_s=0.05)
    time.sleep(0.2)
    g.stop()
    if g.enabled or g.available or vol.db != before or vol.writes:
        fail("keep_gain = false не выключает подстройку")
    ok.append("выключается настройкой keep_gain = false")

    for line in ok:
        print(f"[v] {line}")
    print("\nвсё сошлось")


if __name__ == "__main__":
    main()
