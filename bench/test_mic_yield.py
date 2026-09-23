# -*- coding: utf-8 -*-
"""Микрофон отдаётся только тем, кому он правда нужен.

29.08.2026. Раньше правило звучало так: «держим микрофон, пока человек не ушёл
из окна, куда вставили текст». Уходит он почти всегда — продиктовал в одно
окно, пошёл в другое. Поток закрывался в течение четверти секунды после
надиктовки, и следующее нажатие открывало микрофон заново: 105 миллисекунд
мёртвого времени и, главное, пустая предзапись — полсекунды звука ДО нажатия
брать было неоткуда.

Померено на 2206 надиктовках с 20 по 29 августа: у 68 процентов предзаписи не
было вообще (цифра двугорбая — либо полные 0,5 секунды, либо ровно ноль), и
такие надиктовки приходилось переговаривать или помечать «плохо» втрое чаще:
2,19 процента против 0,72. Даже когда с прошлой надиктовки прошло меньше
минуты и таймер поток ещё не закрыл, 47 процентов приходили пустыми — их
закрывало именно это правило.

Теперь микрофон отдаётся только программам из списка [mic] yield_to.

    ..\\.venv\\Scripts\\python.exe test_mic_yield.py
"""
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stt import paste as paste_mod  # noqa: E402

# Берём саму проверку из программы, не переписывая её здесь: иначе тест
# проверял бы свою копию правила, а не то, что выполняется на самом деле.
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "stt_main_probe", ROOT / "stt" / "__main__.py"
)
_mod = importlib.util.module_from_spec(spec)
_mod.__package__ = "stt"
spec.loader.exec_module(_mod)
MAY_HOLD = _mod.Dictation._may_hold_mic


class Fake:
    """Ровно те поля, которые нужны проверке. Микрофон не трогаем.

    С 31.08.2026 у проверки появилось состояние: программе из списка микрофон
    отдаётся не за то, что её окно впереди, а под запись звука — и на пробу,
    когда окно только что вышло вперёд (подробности в stt/miclisteners.py).
    Здесь каждый раз новый объект, поэтому окно всегда «только что вышло
    вперёд» и проба открыта — это и проверяется ниже.
    """

    def __init__(self, yield_to):
        self.yield_to = {x.lower() for x in yield_to}
        self.yield_probe_s = 2.5
        self.yield_probe_every_s = 25.0
        self.yield_grace_s = 120.0
        self._front_exe = ""
        self._front_since = 0.0
        self._probe_until = 0.0


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def main():
    cfg = tomllib.load(open(ROOT / "config.toml", "rb"))
    listed = cfg["mic"].get("yield_to", [])
    app = Fake(listed)
    ok = []

    real = paste_mod.foreground_exe
    try:
        # --- 1. Обычные рабочие окна микрофон не отбирают ---
        for exe in ["Code.exe", "chrome.exe", "WindowsTerminal.exe",
                    "explorer.exe", "notepad.exe", "msedge.exe"]:
            paste_mod.foreground_exe = lambda e=exe: e
            if not MAY_HOLD(app):
                fail(f"{exe} отобрал микрофон — предзапись снова будет пустой")
        ok.append("рабочие окна (редактор, браузер, терминал) микрофон не трогают")

        # --- 2. Программы для звонков забирают его сразу ---
        # Каждой даём свежий объект: окно «только что вышло вперёд», а значит
        # программа получает пробу — те секунды, за которые звонок успевает
        # открыть микрофон (наш захват эксклюзивный, иначе он его не получит).
        for exe in ["Zoom.exe", "Teams.exe", "Discord.exe"]:
            paste_mod.foreground_exe = lambda e=exe: e
            if MAY_HOLD(Fake(listed)):
                fail(f"{exe} не получил микрофон — звонок найдёт его занятым")
        ok.append("Zoom, Teams, Discord получают микрофон сразу")

        # --- 2b. Программа, которая звук не пишет, микрофон не удерживает ---
        # Slack и Telegram стоят у Антона впереди весь день, а звук за
        # 31.08.2026 не писали ни секунды (Slack — с 13.08). Раньше они
        # забирали микрофон просто за то, что окно впереди: у трети надиктовок
        # того дня предзапись была пустая, то есть первый слог срезан.
        quiet = Fake(listed)
        paste_mod.foreground_exe = lambda: "slack.exe"
        MAY_HOLD(quiet)                    # проба на старте — микрофон отдан
        # Проба кончилась секунду назад, звук Slack так и не запросил.
        quiet._probe_until = time.perf_counter() - 1.0
        quiet._front_exe = "slack.exe"
        if not MAY_HOLD(quiet):
            fail("Slack держит микрофон, не записывая — предзапись снова пустая")
        ok.append("Slack и Telegram, пока не пишут звук, микрофон не держат")

        # --- 3. Регистр имени не важен: Windows пишет их вразнобой ---
        paste_mod.foreground_exe = lambda: "ZOOM.EXE"
        if MAY_HOLD(app):
            fail("имя в верхнем регистре не опознано")
        paste_mod.foreground_exe = lambda: "zoom.exe"
        if MAY_HOLD(app):
            fail("имя в нижнем регистре не опознано")
        ok.append("имя программы узнаётся в любом регистре")

        # --- 4. Не удалось узнать окно — микрофон не отдаём ---
        # Пустая строка приходит, когда окно чужое или прав не хватило. Отдавать
        # микрофон на каждый такой сбой значило бы вернуть пустую предзапись.
        paste_mod.foreground_exe = lambda: ""
        if not MAY_HOLD(app):
            fail("на неизвестном окне микрофон отдан — так теряется начало фразы")
        ok.append("окно не опознано — микрофон остаётся у диктовки")

        # --- 5. Пустой список = старое поведение «держим всегда» ---
        paste_mod.foreground_exe = lambda: "Zoom.exe"
        if not MAY_HOLD(Fake([])):
            fail("с пустым списком микрофон должен держаться всегда")
        ok.append("пустой yield_to — микрофон держится всегда")

        # --- 6. Браузеров в списке быть не должно ---
        # Диктовка весь день идёт в браузер. Один chrome.exe в списке вернул бы
        # те самые 68 процентов надиктовок без предзаписи.
        browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                    "opera.exe", "arc.exe"}
        clash = browsers & {x.lower() for x in listed}
        if clash:
            fail(f"в yield_to попал браузер: {', '.join(sorted(clash))}")
        ok.append("в списке нет браузеров")

        # --- 7. Настройка удержания не должна снова стать короткой ---
        hot_ms = cfg["mic"].get("hot_ms", 0)
        if hot_ms < 600000:
            fail(f"hot_ms = {hot_ms}: при паузе дольше этого предзапись снова "
                 f"пустая (в логах так было у 97% таких надиктовок)")
        ok.append(f"микрофон держится {hot_ms/60000:.0f} мин после надиктовки")
    finally:
        paste_mod.foreground_exe = real

    for line in ok:
        print(f"[v] {line}")
    print("\nвсё сошлось")


if __name__ == "__main__":
    main()
