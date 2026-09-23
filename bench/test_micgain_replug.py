# -*- coding: utf-8 -*-
"""Микрофон переткнули — сторож усиления находит его заново.

04.09.2026 PodMic вынули и воткнули в другой разъём. Для Windows это новый
звуковой узел, а сторож усиления держал ручку старого: каждое чтение падало,
он молча пропускал круг за кругом, и уровень до перезапуска никто не держал.
Теперь после трёх пустых кругов подряд узел ищется заново по имени, а
удерживаемый уровень остаётся прежним — он наш, а не устройства.

Настоящего микрофона нет, устройство подделано.

    ..\\.venv\\Scripts\\python.exe test_micgain_replug.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import micgain as M  # noqa: E402


class FakeVol:
    """Подделка усилителя. dead=True — узел вынут, любой вызов падает."""

    def __init__(self, db=42.84, lo=22.0, hi=63.0):
        self.db = float(db)
        self.lo, self.hi = lo, hi
        self.writes = 0
        self.dead = False

    def _check(self):
        if self.dead:
            raise OSError("AUDCLNT_E_DEVICE_INVALIDATED")

    def GetVolumeRange(self):
        self._check()
        return (self.lo, self.hi, 1.0)

    def GetMasterVolumeLevel(self):
        self._check()
        return self.db

    def SetMasterVolumeLevel(self, value, _ctx):
        self._check()
        self.db = float(value)
        self.writes += 1


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def main():
    ok = []
    tmp = tempfile.mkdtemp()
    cfg = {"mic": {"keep_gain": True, "target_peak": 0.5,
                   "min_db": 34.0, "max_db": 58.0, "max_step_db": 6.0}}
    logs = []

    # Что сейчас «воткнуто»: сторож спрашивает через _endpoint.
    plugged = {"vol": FakeVol(42.84), "name": "Desktop Microphone (RØDE PodMic USB)"}
    M._endpoint = lambda hint: (plugged["vol"], plugged["name"])

    g = M.MicGain("PodMic", cfg, Path(tmp) / "micgain.json", logs.append)
    if not g.open():
        fail("устройство должно найтись")
    g.want_db = 48.0
    old = plugged["vol"]
    lookups = {"n": 0}
    real_endpoint = M._endpoint

    def counting_endpoint(hint):
        lookups["n"] += 1
        return real_endpoint(hint)

    M._endpoint = counting_endpoint
    g.start(period_s=0.05)
    time.sleep(0.2)
    if abs(old.db - 48.0) > 0.1:
        fail(f"до перетыкания уровень не держится: {old.db:.1f}")

    # --- 1. Вынули: старая ручка мертва, сторож не падает и не спамит ---
    old.dead = True
    plugged["vol"] = None                      # искать пока нечего
    time.sleep(0.5)                            # ~10 кругов, ~3 попытки поиска
    if not g._thread.is_alive():
        fail("сторож умер, когда узел пропал")
    if lookups["n"] < 2 or lookups["n"] > 5:
        fail(f"поиск узла идёт не «каждый третий круг»: {lookups['n']} за 10 кругов")
    if any("is back" in m for m in logs):
        fail("сообщил о возвращении, хотя микрофона нет")
    ok.append(f"без микрофона сторож жив, ищет его каждый третий круг ({lookups['n']} раз за 10 кругов)")

    # --- 2. Воткнули в другой разъём: новый узел с уведённым уровнем ---
    new = FakeVol(63.0)                        # Windows выставил по-своему
    plugged["vol"] = new
    plugged["name"] = "Desktop Microphone (2- RØDE PodMic USB)"
    time.sleep(0.5)
    g.stop()
    if g._vol is not new:
        fail("сторож не перешёл на новый узел")
    if abs(new.db - 48.0) > 0.1:
        fail(f"на новом узле уровень не восстановлен: {new.db:.1f} дБ")
    if g.name != plugged["name"]:
        fail(f"имя не обновилось: {g.name}")
    if not any("is back" in m and "2- R" in m and "48.0" in m for m in logs):
        fail(f"в журнале нет строки о возвращении микрофона: {logs}")
    ok.append(f"после перетыкания уровень {new.db:.1f} дБ восстановлен на новом узле, в журнале одна строка")

    # --- 3. Ложных переключений нет: живой узел не переискивается ---
    g2 = M.MicGain("PodMic", cfg, Path(tmp) / "micgain.json", logs.append)
    g2.open()
    lookups["n"] = 0                           # open() — единственный законный поиск
    g2.want_db = 48.0
    g2.start(period_s=0.02)
    time.sleep(0.3)
    g2.stop()
    if lookups["n"]:
        fail(f"живой узел искали заново {lookups['n']} раз")
    ok.append("живой узел заново не ищется")

    print("[v] " + "\n[v] ".join(ok))


if __name__ == "__main__":
    main()
