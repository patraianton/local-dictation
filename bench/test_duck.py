# -*- coding: utf-8 -*-
"""Проверка приглушения чужого звука.

    ..\\.venv\\Scripts\\python.exe test_duck.py

Настоящий Windows тут не нужен: источники звука подделаны, проверяется логика —
кого трогаем, что запоминаем, что возвращаем.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.ducking import Ducker, Session  # noqa: E402


class Fake:
    """Поддельная программа со звуком."""

    def __init__(self, pid, name, vol=1.0):
        self.pid, self.name, self.vol = pid, name, vol
        self.writes = 0

    def session(self):
        def set_(v):
            self.vol = v
            self.writes += 1

        return Session(self.pid, self.name, lambda: self.vol, set_)


class Broken(Fake):
    """Программа, которая не даёт менять громкость."""

    def session(self):
        def boom(_v):
            raise OSError("нет доступа")

        return Session(self.pid, self.name, lambda: self.vol, boom)


def wait(d: Ducker, want_saved: bool, limit: float = 2.0) -> None:
    """Работа идёт в отдельной ниточке — ждём, пока закончится."""
    t0 = time.perf_counter()
    while (bool(d.saved) != want_saved) and time.perf_counter() - t0 < limit:
        time.sleep(0.01)
    time.sleep(0.05)


def make(apps, **over):
    cfg = {"duck": {"enabled": True, "level": 0.15, **over}}
    state = Path(tempfile.mkdtemp()) / "duck.json"
    return Ducker(cfg, state, sessions=lambda: [a.session() for a in apps],
                  own_pid=999), state


def main() -> None:
    bad = 0

    def check(ok, why, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok and detail:
            print(f"      {detail}")

    # 1. приглушает и возвращает
    yt, tg = Fake(1, "chrome.exe"), Fake(2, "Telegram.exe", 0.8)
    d, _s = make([yt, tg])
    d.duck(); wait(d, True)
    check(yt.vol == 0.15 and tg.vol == 0.15, "приглушает всё чужое",
          f"chrome={yt.vol}, telegram={tg.vol}")
    d.restore(); wait(d, False)
    check(yt.vol == 1.0 and tg.vol == 0.8, "возвращает КАЖДОМУ его громкость",
          f"chrome={yt.vol} (ждали 1.0), telegram={tg.vol} (ждали 0.8)")

    # 2. свою громкость не трогаем
    me = Fake(999, "python.exe")
    d, _s = make([me])
    d.duck(); wait(d, True)
    check(me.vol == 1.0 and me.writes == 0, "своей громкости не касается")

    # 3. кто и так тише — не трогаем (иначе сделали бы ГРОМЧЕ)
    quiet = Fake(3, "spotify.exe", 0.05)
    d, _s = make([quiet])
    d.duck(); wait(d, True)
    check(quiet.vol == 0.05 and quiet.writes == 0,
          "того, кто и так тихий, не трогаем", f"вышло {quiet.vol}")

    # 4. исключения
    zoom, ch = Fake(4, "Zoom.exe"), Fake(5, "chrome.exe")
    d, _s = make([zoom, ch], exclude=["zoom.exe"])
    d.duck(); wait(d, True)
    check(zoom.vol == 1.0 and ch.vol == 0.15, "исключённых не глушит",
          f"zoom={zoom.vol}, chrome={ch.vol}")

    # 5. одна сломанная программа не мешает остальным
    ok_app, boom = Fake(6, "vlc.exe"), Broken(7, "битая.exe")
    d, _s = make([ok_app, boom])
    d.duck(); wait(d, True)
    check(ok_app.vol == 0.15, "сломанный источник не ломает остальные")
    d.restore(); wait(d, False)
    check(ok_app.vol == 1.0, "и возврат тоже проходит")

    # 6. выключено в настройках — ничего не делаем
    app = Fake(8, "chrome.exe")
    cfg = {"duck": {"enabled": False}}
    d = Ducker(cfg, Path(tempfile.mkdtemp()) / "d.json",
               sessions=lambda: [app.session()], own_pid=999)
    d.duck(); time.sleep(0.1)
    check(app.vol == 1.0 and app.writes == 0, "выключенное приглушение молчит")

    # 7. страховка: программу убили во время записи — при запуске вернём
    app = Fake(9, "chrome.exe")
    d, state = make([app])
    d.duck(); wait(d, True)
    check(state.exists(), "запомненная громкость записана в файл")
    app.vol = 0.15                     # как будто программу убили, звук остался тихим
    d2 = Ducker({"duck": {"enabled": True, "level": 0.15}}, state,
                sessions=lambda: [app.session()], own_pid=999)
    n = d2.recover()
    check(n == 1 and app.vol == 1.0, "после падения громкость возвращается",
          f"вернул {n} шт., громкость {app.vol}")
    check(not state.exists(), "файл-страховка после возврата убирается")

    # 8. чужой процесс занял тот же номер — громкость ему не крутим
    state2 = Path(tempfile.mkdtemp()) / "duck.json"
    state2.write_text(json.dumps({"10": ["chrome.exe", 1.0]}), encoding="utf-8")
    other = Fake(10, "notepad.exe", 0.3)
    d3 = Ducker({"duck": {"enabled": True}}, state2,
                sessions=lambda: [other.session()], own_pid=999)
    d3.recover()
    check(other.vol == 0.3 and other.writes == 0,
          "чужой программе с тем же номером процесса громкость не меняем")

    # 9. быстрый тык: приглушить и сразу вернуть — звук не остаётся тихим
    app = Fake(11, "chrome.exe")
    d, _s = make([app])
    d.duck()
    d.restore()
    time.sleep(0.4)
    check(app.vol == 1.0, "быстрое нажатие не оставляет звук приглушённым",
          f"вышло {app.vol}")

    print(f"\n{'всё сошлось' if not bad else str(bad) + ' не сошлось'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
