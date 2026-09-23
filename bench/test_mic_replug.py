# -*- coding: utf-8 -*-
"""Микрофон переткнули — диктовка находит его заново, без перезапуска.

04.09.2026 в 11:39 PodMic вынули и воткнули в другой разъём. Для Windows это
новое устройство («2- RØDE PodMic USB»), а звуковая библиотека (PortAudio)
читает список устройств один раз при старте и больше никогда. Старый номер
устройства (43) с этого момента отвечает «Invalid device», и до перезапуска
в 12:28 ни одна надиктовка не записалась.

Что проверяется:
  1. Открытие по запомненному номеру не удалось — таблица устройств
     перечитывается, микрофон ищется по имени и открывается заново.
  2. Микрофон вынули, пока поток держался открытым: Windows молчит, просто
     перестают приходить кадры. Такой «мёртвый» поток замечается за секунду.
  3. Раз открыв СВОЙ микрофон по «сырому» пути, диктовка больше никогда не
     соскальзывает ни на «общий» путь через обработку Windows, ни на другой
     микрофон (гарнитуру, веб-камеру): занят — ждём, вынут — ждём.
  4. Если при старте свой микрофон был занят или отсутствовал, диктовка
     работает на подмене, но каждые relook_s ищет свой и переходит на него.
  5. Сторож не закрывает поток в щель между открытием и началом записи.

Настоящего микрофона здесь нет — звуковая карта подделана: у одного
микрофона несколько строк в таблице (как в Windows), «устройство по
умолчанию» — это другой микрофон, «занят» — держат только сырой путь.

    ..\\.venv\\Scripts\\python.exe test_mic_replug.py
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

RAW, SHARED = "Windows WDM-KS", "Windows WASAPI"
PODMIC = "Desktop Microphone (RØDE PodMic USB)"
PODMIC2 = "Desktop Microphone (2- RØDE PodMic USB)"
HEADSET = "Microphone (HyperX Cloud Alpha Wireless)"


class FakeSD:
    """Подделка звуковой библиотеки с «железом», которое можно перетыкать.

    real   — что воткнуто прямо сейчас: номер -> (имя, путь).
    table  — что видит библиотека: снимок real на момент _initialize().
    """

    def __init__(self, real, default=None):
        self.real = dict(real)
        self.table = dict(real)
        self.default = default       # что Windows считает микрофоном по умолчанию
        self.terminated = 0
        self.initialized = 0
        self.busy_raw = False        # сырой путь держит другая программа
        self.streams = []
        fake = self

        class InputStream:
            def __init__(self, samplerate, channels, dtype, device, callback,
                         blocksize):
                if device is None:
                    device = fake.default
                # Номер должен быть в таблице библиотеки И устройство должно
                # быть воткнуто: старый номер после перетыкания есть в
                # таблице, но за ним уже ничего нет — ровно так отвечал
                # настоящий PortAudio 04.09.2026.
                if (device is None or device not in fake.table
                        or device not in fake.real):
                    raise Exception("Error opening InputStream: Invalid "
                                    "device [PaErrorCode -9996]")
                if fake.busy_raw and fake.real[device][1] == RAW:
                    raise Exception("Error opening InputStream: Device "
                                    "unavailable [PaErrorCode -9985]")
                self.device = device
                self.cb = callback
                self.value = 0.0
                self._stop = threading.Event()
                fake.streams.append(self)

            def start(self):
                def loop():
                    t0, sent = time.perf_counter(), 0
                    while not self._stop.wait(0.005):
                        # Микрофон вынули — кадры перестают приходить,
                        # никакой ошибки нет. Так ведёт себя настоящий.
                        if self.device not in fake.real:
                            continue
                        want = int((time.perf_counter() - t0) * SR)
                        while sent < want:
                            n = min(BLOCK, want - sent)
                            self.cb(np.full((n,), self.value, dtype=np.float32),
                                    n, None, None)
                            sent += n
                threading.Thread(target=loop, daemon=True).start()

            def stop(self):
                self._stop.set()

            def close(self):
                self._stop.set()

        self.InputStream = InputStream

    # --- то, что зовёт stt.audio ---
    def _terminate(self):
        self.terminated += 1

    def _initialize(self):
        self.initialized += 1
        self.table = dict(self.real)

    def _entry(self, idx):
        name, api = self.table[idx]
        return {"name": name, "max_input_channels": 1,
                "hostapi": 0 if api == RAW else 1, "default_samplerate": SR}

    def query_devices(self, device=None, kind=None):
        if device is None:
            return [self._entry(i) for i in self.table]
        if device not in self.table:
            raise Exception("Error querying device")
        return self._entry(device)

    def query_hostapis(self, index=None):
        return [{"name": RAW}, {"name": SHARED}]

    # --- «железо» ---
    def unplug(self, *idx):
        for i in idx:
            self.real.pop(i, None)

    def plug(self, entries):
        self.real.update(entries)

    def finder(self):
        """Как find_devices: по имени, из таблицы библиотеки, сырой путь первым."""
        hits = [(i, api) for i, (n, api) in self.table.items() if "podmic" in n.lower()]
        hits.sort(key=lambda x: 0 if x[1] == RAW else 1)
        return [i for i, _ in hits] + [None]


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def wait_for(cond, timeout_s, what):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if cond():
            return
        time.sleep(0.02)
    fail(f"за {timeout_s} с не дождались: {what}")


def alive(stream):
    return stream is not None and not stream._stop.is_set()


def podmic_table(idx_raw=43, idx_shared=30, name=PODMIC):
    return {idx_raw: (name, RAW), idx_shared: (name, SHARED), 2: (HEADSET, SHARED)}


def make(sd, hot_s, logs=None, **kw):
    A.sd = sd
    rec = A.Recorder(sd.finder(), SR, preroll_s=0.5, hot_s=hot_s,
                     finder=sd.finder, named=True, **kw)
    if logs is not None:
        rec.log = logs.append
    return rec


def main():
    ok = []
    logs = []

    # --- 1. Сегодняшний случай: поток отпущен по времени, микрофон переткнут,
    #        нажатие клавиши — старый номер мёртв, ищем заново ---
    sd = FakeSD(podmic_table(), default=2)
    rec = make(sd, hot_s=0.0, logs=logs)
    rec.start(); time.sleep(0.05); rec.stop()          # hot_s=0: отпущен сразу
    if rec.recipe[0] != 43 or not rec.settled:
        fail(f"до перетыкания открылось не то: {rec.recipe}, settled={rec.settled}")
    sd.unplug(43, 30)
    sd.plug({51: (PODMIC2, RAW), 52: (PODMIC2, SHARED)})
    t0 = time.perf_counter()
    rec.start()
    took = time.perf_counter() - t0
    if rec.recipe[0] != 51:
        fail(f"после перетыкания открылся не сырой путь нового номера: {rec.recipe}")
    if (sd.terminated, sd.initialized) != (1, 1):
        fail(f"таблицу перечитали не один раз: {sd.terminated}/{sd.initialized}")
    rec._stream.value = 0.5
    time.sleep(0.3)
    data = rec.stop()
    if data.size == 0 or abs(float(np.median(data[200:])) - 0.5) > 0.02:
        fail("после перетыкания записалось не то, что «слышно»")
    if not any("found again" in m and "2- R" in m for m in logs):
        fail(f"в журнале нет строки о том, что микрофон найден заново: {logs}")
    ok.append(f"переткнули при отпущенном микрофоне — найден заново за {took*1000:.0f} мс, сырой путь")

    # --- 2. Микрофон вынули, пока поток держался: мёртвый поток замечен;
    #        пока микрофона нет — НЕ садимся на гарнитуру; воткнули — подхвачен
    #        сам, ещё до нажатия, и снова по сырому пути ---
    logs.clear()
    sd = FakeSD(podmic_table(), default=2)
    rec = make(sd, hot_s=30.0, logs=logs)
    if not rec.warm():
        fail(f"не открылся: {rec.last_error}")
    time.sleep(0.3)
    held_before = rec._stream
    sd.unplug(43, 30)                                   # кадры перестают идти
    wait_for(lambda: not alive(held_before), 2.5, "мёртвый поток отпущен")
    time.sleep(1.0)                                     # сторож стучится
    if rec._stream is not None:
        fail(f"без своего микрофона открыли чужой: {rec.recipe}")
    if not any("not plugged in" in m for m in logs):
        fail(f"в журнале нет строки «микрофон не воткнут»: {logs}")
    sd.plug({51: (PODMIC2, RAW), 52: (PODMIC2, SHARED)})
    wait_for(lambda: rec.recipe and rec.recipe[0] == 51 and alive(rec._stream),
             14.0, "микрофон подхвачен на новом номере")
    time.sleep(0.6)                                     # набрать предзапись
    rec.start()
    pre = rec._pending_preroll
    rec._stream.value = 0.75
    time.sleep(0.3)
    data = rec.stop()
    if pre < 0.4:
        fail(f"после втыкания предзапись пуста: {pre:.2f} с")
    if data.size == 0 or abs(float(np.median(data[-2000:])) - 0.75) > 0.02:
        fail("после втыкания записалось не то, что «слышно»")
    n_absent = sum("not plugged in" in m for m in logs)
    n_back = sum("found again" in m for m in logs)
    rec.close()
    if (n_absent, n_back) != (1, 1):
        fail(f"журнал засорён: «не воткнут» {n_absent} раз, «найден» {n_back} раз")
    ok.append(f"вынули при удержании — поток отпущен, чужой не взят, после втыкания подхвачен, предзапись {pre*1000:.0f} мс")

    # --- 3. Сырой путь занят (звонок в браузере): НЕ сползаем на общий путь,
    #        таблицу перечитываем не чаще раза в десять секунд, после звонка
    #        снова сырой путь ---
    sd = FakeSD(podmic_table(), default=2)
    rec = make(sd, hot_s=0.0)
    rec.start(); rec.stop()
    sd.busy_raw = True
    for _ in range(5):
        try:
            rec.start()
            fail(f"при занятом сыром пути «открылось»: {rec.recipe}")
        except RuntimeError:
            pass
    if sd.terminated != 1:
        fail(f"на пять сбоев подряд таблицу перечитали {sd.terminated} раз")
    if any(s.device == 30 for s in sd.streams):
        fail("при занятом сыром пути открыли общий путь")
    sd.busy_raw = False
    rec.start(); rec.stop()
    if rec.recipe[0] != 43:
        fail(f"после звонка открылось не то: {rec.recipe}")
    ok.append("занят сырой путь — ждём, на общий не сползаем, таблица перечитана один раз")

    # --- 4. Микрофона нет вовсе: честная ошибка, ничего не висит ---
    sd = FakeSD(podmic_table(), default=2)
    rec = make(sd, hot_s=0.0)
    rec.start(); rec.stop()
    sd.unplug(43, 30)
    try:
        rec.start()
        fail("без микрофона запись «началась»")
    except RuntimeError as exc:
        if "Invalid device" not in str(exc) or "not plugged in" not in str(exc):
            fail(f"не та ошибка: {exc}")
    if rec._stream is not None:
        fail("без микрофона остался открытый поток")
    ok.append("микрофона нет — ошибка с понятной причиной, ничего не висит")

    # --- 5. Старый вызов (без finder, без имени): тот же номер, новая таблица ---
    sd = FakeSD({43: (PODMIC, RAW)})
    A.sd = sd
    rec = A.Recorder([43], SR, preroll_s=0.5, hot_s=0.0)
    rec.start(); rec.stop()
    sd.table = {}                                       # библиотека потеряла всё
    rec.start(); rec.stop()
    if rec.recipe[0] != 43 or sd.initialized != 1:
        fail(f"без finder не переоткрылся: {rec.recipe}, init {sd.initialized}")
    ok.append("старый вызов без finder: таблица перечитана, номер тот же")

    # --- 6. Без сбоев ничего не перечитывается, предзапись на месте ---
    sd = FakeSD(podmic_table(idx_raw=7, idx_shared=8), default=2)
    rec = make(sd, hot_s=5.0)
    rec.warm()
    time.sleep(0.7)
    rec.start()
    if not 0.4 <= rec._pending_preroll <= 0.6:
        fail(f"предзапись не полсекунды: {rec._pending_preroll:.2f}")
    rec.stop()
    if sd.terminated:
        fail("без сбоев таблицу перечитывать не должны")
    rec.close()
    ok.append("без сбоев ничего не перечитывается, предзапись на месте")

    # --- 7. При старте своего микрофона нет: работаем на гарнитуре, но ищем
    #        свой; воткнули — перешли на него и больше с него не уходим ---
    logs.clear()
    sd = FakeSD({2: (HEADSET, SHARED)}, default=2)
    rec = make(sd, hot_s=30.0, logs=logs)
    rec.relook_s = 0.5
    if not rec.warm():
        fail(f"без своего микрофона подмена не открылась: {rec.last_error}")
    if rec.recipe[0] is not None or rec.settled:
        fail(f"подмена принята за свой микрофон: {rec.recipe}, settled={rec.settled}")
    time.sleep(1.2)
    if rec.recipe[0] is not None:
        fail("пока своего нет, ушли с подмены")
    sd.plug(podmic_table())
    wait_for(lambda: rec.recipe and rec.recipe[0] == 43 and rec.settled,
             3.0, "переход с подмены на свой микрофон")
    n_init = sd.initialized
    time.sleep(1.2)
    if sd.initialized != n_init:
        fail("на своём микрофоне продолжаем перечитывать таблицу")
    sd.unplug(43, 30)
    time.sleep(2.0)
    if rec._stream is not None or (rec.recipe and rec.recipe[0] is None):
        fail(f"после пропажи своего микрофона вернулись на подмену: {rec.recipe}")
    rec.close()
    ok.append("при старте своего нет — работаем на подмене, воткнули — перешли и не уходим")

    # --- 8. При старте сырой путь занят: работаем на общем, звонок кончился —
    #        вернулись на сырой ---
    sd = FakeSD(podmic_table(), default=2)
    sd.busy_raw = True
    rec = make(sd, hot_s=30.0)
    rec.relook_s = 0.5
    if not rec.warm() or rec.recipe[0] != 30 or rec.settled:
        fail(f"при занятом сыром пути подмена не та: {rec.recipe}, settled={rec.settled}")
    sd.busy_raw = False
    wait_for(lambda: rec.recipe and rec.recipe[0] == 43 and rec.settled,
             3.0, "возврат на сырой путь после звонка")
    rec.close()
    ok.append("при старте сырой путь занят — работаем на общем, освободился — вернулись")

    # --- 9. Сторож решил отпустить микрофон, а в это мгновение нажали клавишу:
    #        запись не должна остаться без потока ---
    sd = FakeSD(podmic_table(), default=2)
    rec = make(sd, hot_s=30.0)
    rec.warm()
    time.sleep(0.3)
    gate = threading.Event()

    def slow_veto():
        gate.set()
        time.sleep(0.15)          # сторож «думает» — как чтение реестра
        return False              # ...и решает отпустить микрофон

    rec.should_hold = slow_veto
    gate.wait(2.0)
    rec.start()                   # клавиша нажата, пока сторож думает
    time.sleep(0.4)               # сторож дошёл до закрытия
    if rec._stream is None or not rec._armed:
        fail("сторож закрыл поток под начатой записью")
    rec._stream.value = 0.6
    time.sleep(0.3)
    rec.should_hold = None
    data = rec.stop()
    if data.size == 0 or abs(float(np.median(data[-2000:])) - 0.6) > 0.02:
        fail("запись, начатая в момент решения сторожа, потеряна")
    rec.close()
    ok.append("нажатие в момент решения сторожа — запись цела")

    print("[v] " + "\n[v] ".join(ok))


if __name__ == "__main__":
    main()
