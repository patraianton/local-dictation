# -*- coding: utf-8 -*-
"""Диктовка: зажал клавишу — сказал — текст в окне.

Запуск:
    run.ps1              — работать
    run.ps1 mics         — какие есть микрофоны
    run.ps1 keytest      — какой код у клавиши
    run.ps1 selftest     — всё ли на месте
    run.ps1 bench FILE   — сколько секунд занимает распознавание
"""
import io
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from . import audio as audio_mod  # noqa: E402
from . import config as cfg_mod  # noqa: E402
from . import ducking  # noqa: E402
from . import endings  # noqa: E402
from .fixes import Fixes  # noqa: E402
from .hud import Hud  # noqa: E402
from .learn import Learner  # noqa: E402
from .paste import paste_text  # noqa: E402
from .polish import Polisher  # noqa: E402

MIN_SECONDS = 0.25
SILENCE_RMS = 0.0015


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Dictation:
    def __init__(self):
        cfg_mod.ensure_dirs()
        self.cfg = cfg_mod.load()
        self.terms = cfg_mod.glossary()
        self.fixes = Fixes(cfg_mod.FIXES_PATH)
        self.mywords = cfg_mod.mywords()
        self.fix_endings = bool(self.cfg.get("endings", {}).get("enabled", True))
        self.polisher = Polisher(self.cfg, self.terms, self.fixes, self.mywords)
        self.learner = Learner(
            self.cfg, self.fixes, cfg_mod.LOG_DIR, cfg_mod.REC_DIR,
            cfg_mod.CANDIDATES_PATH,
        )
        self.ducker = ducking.Ducker(self.cfg, cfg_mod.DUCK_STATE_PATH)
        self.hud = Hud(self.cfg)

        hk = self.cfg["hotkey"]
        self.key = hk["scancode"] if int(hk.get("scancode", 0)) else hk.get("name", "f13")
        self.fix_hotkey = hk.get("fix_hotkey", "shift+f13")
        self.flip_hotkey = hk.get("flip_hotkey", "ctrl+f13")
        self.tap_ms = int(hk.get("tap_ms", 350))
        self.max_seconds = int(hk.get("max_seconds", 300))
        mic_cfg = self.cfg.get("mic", {})
        self.tail_s = float(mic_cfg.get("tail_ms", 400)) / 1000.0
        self.tail_quiet_ms = int(mic_cfg.get("tail_quiet_ms", 120))

        self.devices = audio_mod.find_devices(self.cfg["mic"].get("name", ""))
        self.device = self.devices[0]
        self.recorder = audio_mod.Recorder(
            self.devices, int(self.cfg["mic"]["samplerate"])
        )
        self.mic_cooldown = 0.0

        self.asr = None
        self.recording = False
        self.locked = False
        self.t_down = 0.0
        self.busy = threading.Lock()
        self.last: dict = {}
        self.mouse_hook = None

    # ---------- загрузка ----------
    def boot(self) -> None:
        from .asr import Asr

        self.hud.set("think", "гружусь")
        mic_name = "по умолчанию"
        if self.device is not None:
            mic_name = audio_mod.sd.query_devices(self.device)["name"]
        log(f"микрофон: {mic_name}")

        self.asr = Asr(self.cfg, self.terms)
        took = self.asr.load()
        log(f"распознавалка {self.asr.model_name} на {self.asr.device}: {took:.1f} с")
        warm = self.asr.warmup()
        log(f"прогрев: {warm:.2f} с")

        if self.polisher.check():
            warm = self.polisher.warmup()
            log(f"причёсывание: {self.polisher.model} (подняли за {warm:.1f} с)")
        else:
            log(f"причёсывание ВЫКЛЮЧЕНО — {self.polisher.reason}")
            log("  (диктовка работает и без него, текст будет как есть)")

        log(f"словарь замен: {len(self.fixes)} пар, терминов в подсказке: {len(self.terms)}")
        web = self.cfg.get("web", {})
        if web.get("enabled", True):
            from . import server

            try:
                url = server.start(
                    int(web.get("port", 8756)), self.fixes, self.reload_terms,
                    self.polisher,
                )
                log(f"страница диктовок: {url}")
                if web.get("open_on_start", False):
                    import webbrowser

                    webbrowser.open(url)
            except Exception as exc:
                log(f"страница не поднялась: {exc}")

        # Если прошлый раз программу убили во время записи, чужой звук остался
        # приглушённым — возвращаем его обратно.
        self.ducker.recover()
        if self.ducker.enabled:
            log(f"чужой звук на время записи: до {self.ducker.level*100:.0f}%")

        self.bind_keys()
        threading.Thread(target=self._keep_warm, daemon=True).start()
        self.hud.set("ok", "готово", hide_after=1.5)
        log(f"ГОТОВО. Клавиша {self.key}: зажать = говорить, короткое нажатие = без рук.")
        log(f"Поправить последнее: {self.fix_hotkey}. Отмена записи: Esc.")
        if self.flip_hotkey:
            log(f"Точка <-> вопрос в конце: {self.flip_hotkey}.")

    def reload_terms(self) -> None:
        """Перечитывает термины на ходу — без перезапуска программы.

        Слово, добавленное на странице, должно работать со следующей же
        диктовки: и в подсказке распознавалке, и у корректора.
        """
        from .asr import build_prompt
        from .polish import allowed_words

        self.terms = cfg_mod.glossary()
        self.mywords = cfg_mod.mywords()
        if self.asr is not None:
            a = self.cfg["asr"]
            self.asr.prompt = build_prompt(
                self.terms, int(a.get("prompt_terms", 45)),
                a.get("prompt_style", "list"),
            )
        self.polisher.terms = self.terms
        self.polisher.allowed = allowed_words(self.terms, self.fixes)
        self.polisher.protected = self.mywords
        log(f"термины перечитаны: {len(self.terms)} шт")

    def _keep_warm(self) -> None:
        """Держим корректора в видеопамяти.

        LM Studio выгружает модель после простоя, и первая диктовка после
        перерыва ждёт её загрузку (замерено: 2,1 с вместо 0,2). Тихо
        напоминаем о себе раз в 10 минут.
        """
        while True:
            time.sleep(600)
            if self.busy.locked():
                continue
            try:
                if self.polisher.available or self.polisher.check(force=True):
                    self.polisher.warmup()
            except Exception:
                pass

    def bind_keys(self) -> None:
        import keyboard

        keyboard.on_press_key(self.key, self.on_down, suppress=False)
        keyboard.on_release_key(self.key, self.on_up, suppress=False)
        keyboard.on_press_key("esc", self.on_esc, suppress=False)
        try:
            keyboard.add_hotkey(self.fix_hotkey, self.on_fix, suppress=False)
        except Exception as exc:
            log(f"клавиша правки {self.fix_hotkey} не встала: {exc}")
        if self.flip_hotkey:
            try:
                keyboard.add_hotkey(
                    self.flip_hotkey, self.on_flip_question, suppress=False
                )
            except Exception as exc:
                log(f"клавиша знака {self.flip_hotkey} не встала: {exc}")

        rp = self.cfg.get("repaste", {})
        if not rp.get("enabled", True):
            return
        if rp.get("key"):
            try:
                keyboard.add_hotkey(rp["key"], self.on_repaste, suppress=False)
                log(f"вставить ещё раз: клавиша {rp['key']}")
            except Exception as exc:
                log(f"клавиша {rp['key']} не встала: {exc}")
        if rp.get("button"):
            from . import mousehook

            self.mouse_hook = mousehook.Hook(
                rp["button"], self.on_repaste, bool(rp.get("suppress", False))
            )
            try:
                if self.mouse_hook.start():
                    mode = "перехватываю" if rp.get("suppress", False) else "не перехватываю"
                    log(f"вставить ещё раз: боковые кнопки мыши "
                        f"({self.mouse_hook.names}, {mode})")
                else:
                    log(f"кнопки мыши «{rp['button']}» не опознаны")
            except Exception as exc:
                log(f"мышь не подцепилась: {exc}")

    # ---------- клавиша ----------
    def on_down(self, _event=None) -> None:
        if self.recording:
            if self.locked:
                self.stop_and_process()
            return  # автоповтор клавиши
        self.start()

    def on_up(self, _event=None) -> None:
        if not self.recording or self.locked:
            return
        held_ms = (time.perf_counter() - self.t_down) * 1000
        if held_ms < self.tap_ms:
            self.locked = True
            self.hud.set("lock", "пишу без рук")
        else:
            self.stop_and_process()

    def on_esc(self, _event=None) -> None:
        if self.recording:
            self.recording = self.locked = False
            self.recorder.stop()
            self.ducker.restore()
            self.hud.set("warn", "отменено", hide_after=1.0)
            log("запись отменена")

    def on_repaste(self) -> None:
        """Вставить последнюю диктовку в окно под мышкой.

        Для случая «текст улетел не туда»: наводишь на нужное окно, жмёшь
        кнопку. Программа делает это окно активным и вставляет.
        """
        text = (self.last or {}).get("final", "")
        if not text:
            self.hud.set("warn", "нечего вставлять", hide_after=1.2)
            return
        from . import mousehook

        hwnd = mousehook.window_under_cursor()
        title = mousehook.window_title(hwnd)
        if not mousehook.focus(hwnd):
            self.hud.set("err", "окно не отдало фокус", hide_after=2.0)
            log(f"не смог активировать окно: {title!r}")
            return
        time.sleep(0.06)  # окну нужно мгновение, чтобы принять фокус
        paste_text(
            text,
            self.cfg["paste"].get("hotkey", "ctrl+v"),
            float(self.cfg["paste"].get("restore_clipboard_after_s", 1.0)),
        )
        self.hud.set("ok", "вставил", hide_after=1.0)
        log(f"вставил ещё раз в окно: {title[:60]!r}")

    def on_flip_question(self) -> None:
        """Меняет знак в конце последней диктовки: точка <-> вопрос.

        Зачем это руками. «Скоро это уже закончится» и «Скоро это уже
        закончится?» — одни и те же слова, разница только в голосе. Замерено
        14.08.2026: по голосу вопрос не ловится совсем (2 из 117 на его
        записях), подсказка предыдущими репликами и прямой вопрос модели делают
        только хуже. Значит, последнее слово за человеком — но одной кнопкой,
        а не через окно правки.

        Правит и то, что уже вставлено в окно: стирает последний знак и ставит
        нужный. Курсор при этом должен стоять сразу после вставленного текста —
        так оно и есть, если нажать сразу после диктовки.
        """
        text = (self.last or {}).get("final", "")
        if not text.strip():
            self.hud.set("warn", "нечего править", hide_after=1.2)
            return

        import keyboard

        from .polish import flip_question

        new_text, erase, want = flip_question(text)
        self.last["final"] = new_text

        # правим уже вставленный текст: лишний знак стереть, нужный набрать
        try:
            for _ in range(erase):
                keyboard.send("backspace")
                time.sleep(0.02)
            keyboard.write(want)
        except Exception as exc:
            log(f"поправить в окне не вышло: {exc}")

        # запоминаем правку: она идёт и на страницу, и в материал для обучения
        rec_id = (self.last or {}).get("id")
        if rec_id:
            try:
                from . import store

                res = store.set_text(rec_id, self.last["final"], self.fixes)
                if res.get("learned"):
                    self.fixes.load()
            except Exception as exc:
                log(f"правку не записал: {exc}")

        self.hud.set("ok", f"поставил «{want}»", hide_after=1.2)
        log(f"знак в конце -> «{want}»")
        log(f"  {self.last['final']}")

    def on_fix(self) -> None:
        if not self.last or self.hud.root is None:
            self.hud.set("warn", "нечего править", hide_after=1.2)
            return
        from .fixwin import open_window

        def done(learned: int):
            self.hud.set("ok", f"запомнил: {learned}", hide_after=1.8)
            log(f"запомнил пар: {learned} (всего {len(self.fixes)})")

        self.hud.root.after(
            0, lambda: open_window(self.hud.root, self.last, self.fixes, done)
        )

    # ---------- запись ----------
    def start(self) -> None:
        # Клавиша при удержании стреляет автоповтором. Если микрофон не открылся,
        # без паузы получим сотню одинаковых попыток в секунду.
        if time.perf_counter() < self.mic_cooldown:
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.mic_cooldown = time.perf_counter() + 3.0
            self.hud.set("err", "микрофон не открылся", hide_after=3.0)
            log(f"микрофон не открылся: {exc}")
            return
        self.recording, self.locked = True, False
        self.t_down = time.perf_counter()
        self.ducker.duck()      # чужой звук потише, пока говоришь
        self.hud.set("rec", "")
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self) -> None:
        started = time.perf_counter()
        while self.recording:
            if time.perf_counter() - started > self.max_seconds:
                log("сработала страховка по времени")
                self.stop_and_process()
                return
            time.sleep(0.25)

    def stop_and_process(self) -> None:
        if not self.recording:
            return
        self.recording = self.locked = False
        self.hud.set("think", "думаю")
        # Останавливаем в стороне: дозапись хвоста ждёт до долей секунды, и на
        # ниточке клавиатуры это ожидание держало бы все остальные клавиши.
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        data = self.recorder.stop(
            tail_s=self.tail_s, quiet_ms=self.tail_quiet_ms
        )
        self.ducker.restore()
        self.process(data)

    # ---------- обработка ----------
    def transcribe_resilient(self, audio: np.ndarray) -> tuple[str, float]:
        """Распознаёт, а если видеокарта отвалилась — поднимает модель заново.

        Зачем. 19.08.2026 компьютер поспал, контекст видеокарты умер, и программа
        четыре дня отвечала «СБОЙ» на каждую диктовку. Сама она из этого выйти не
        могла: модель в памяти была мертва, а перезагружать её было некому.
        """
        try:
            return self.asr.transcribe(audio)
        except Exception as exc:
            if not self.asr.looks_like_lost_gpu(exc):
                raise
            log(f"видеокарта отвалилась ({type(exc).__name__}), поднимаю модель заново")
            self.hud.set("think", "поднимаю модель")
            where = self.asr.reload()
            if where == "cpu":
                log("не вышло — перешёл на процессор. Будет медленнее.")
                log(r"Починится перезапуском: .\start-background.ps1 -Restart")
            else:
                log(f"модель снова на {where}")
            return self.asr.transcribe(audio)

    def process(self, data: np.ndarray) -> None:
        if not self.busy.acquire(blocking=False):
            log("предыдущая диктовка ещё считается — пропускаю")
            return
        try:
            t_all = time.perf_counter()
            secs = len(data) / audio_mod.TARGET_SR
            peak, rms = audio_mod.loudness(data)
            if secs < MIN_SECONDS:
                self.hud.set("warn", "слишком коротко", hide_after=1.2)
                return
            if rms < SILENCE_RMS:
                self.hud.set("warn", "тишина в микрофоне", hide_after=2.0)
                log(f"тишина: {secs:.1f} с, пик {peak:.4f}")
                return

            raw, t_asr = self.transcribe_resilient(audio_mod.normalize(data))
            if not raw.strip():
                self.hud.set("warn", "ничего не разобрала", hide_after=1.5)
                return

            pre, n_pre = self.fixes.apply(raw)
            if self.fix_endings:
                pre, flipped = endings.apply(pre)
            else:
                flipped = []
            polished, t_pol, note = self.polisher.polish(pre)
            final, _ = self.fixes.apply(polished)

            paste_text(
                final,
                self.cfg["paste"].get("hotkey", "ctrl+v"),
                float(self.cfg["paste"].get("restore_clipboard_after_s", 1.0)),
            )

            total = time.perf_counter() - t_all
            promoted = self.learner.observe(pre, polished)
            stamp = datetime.now().strftime("%H%M%S-%f")[:-3]
            wav = self.learner.save_audio(data, final, stamp)
            rec_id = f"{datetime.now():%Y-%m-%d}_{stamp}"
            self.last = {"raw": raw, "final": final, "id": rec_id}
            self.learner.log(
                {
                    "id": rec_id,
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "seconds_audio": round(secs, 2),
                    "raw": raw,
                    "after_fixes": pre,
                    "final": final,
                    "polish_note": note,
                    "ms_asr": int(t_asr * 1000),
                    "ms_polish": int(t_pol * 1000),
                    "ms_total": int(total * 1000),
                    "fixes_applied": n_pre,
                    "endings_fixed": flipped,
                    "learned": promoted,
                    "wav": wav,
                }
            )
            self.hud.set("ok", f"{total:.1f} с", hide_after=1.2)
            log(f"{secs:.1f} с речи -> {total:.2f} с "
                f"(распознала {t_asr:.2f}, причесала {t_pol:.2f}, {note})")
            log(f"  {final}")
            for src, dst in flipped:
                log(f"  вернула приказ: «{src}» -> «{dst}»")
            for src, dst in promoted:
                log(f"  запомнила: «{src}» -> «{dst}»")
        except Exception as exc:
            self.hud.set("err", "сбой", hide_after=2.5)
            log(f"СБОЙ: {type(exc).__name__}: {exc}")
        finally:
            self.busy.release()

    def run(self) -> None:
        threading.Thread(target=self.boot, daemon=True).start()
        try:
            self.hud.run()
        except KeyboardInterrupt:
            pass


# ---------- вспомогательные команды ----------
def cmd_mics() -> None:
    for d in audio_mod.list_inputs():
        print(f"{d['index']:>3}  {d['hostapi']:<20} {d['name']}  "
              f"({d['default_samplerate']} Гц, {d['channels']} кан.)")


def cmd_keytest(seconds: int = 12) -> None:
    """Ловит нажатия несколько секунд и говорит, долетает ли нужная клавиша."""
    import keyboard

    cfg = cfg_mod.load()
    want = cfg["hotkey"].get("name", "f13")
    try:
        want_codes = set(keyboard.key_to_scan_codes(want))
    except Exception:
        want_codes = set()

    print(f"Жми клавишу, которую хочешь под диктовку. Слушаю {seconds} секунд.")
    print(f"(в настройках сейчас стоит «{want}»)\n")

    pressed: dict = {}

    def show(e):
        if e.event_type != "down":
            return
        key = (e.name, e.scan_code)
        if key in pressed:
            return
        pressed[key] = True
        mark = "  <-- ЭТА И СТОИТ В НАСТРОЙКАХ" if e.scan_code in want_codes else ""
        print(f"  имя: {str(e.name)!r:<14} скан-код: {e.scan_code}{mark}")

    keyboard.hook(show)
    time.sleep(seconds)
    keyboard.unhook_all()

    print()
    if not pressed:
        print("Ни одного нажатия не поймал.")
        return
    hit = [k for k in pressed if k[1] in want_codes]
    if hit:
        print(f"ГОДИТСЯ: клавиша «{want}» долетает, менять ничего не надо.")
    else:
        names = ", ".join(f"{k[0]} (код {k[1]})" for k in pressed)
        print(f"Клавиша «{want}» НЕ долетела. Поймал вот это: {names}")
        print("Впиши подходящее в config.toml -> [hotkey] name или scancode.")


def cmd_selftest() -> None:
    ok = True
    cfg = cfg_mod.load()
    print("=== проверка ===\n")

    hint = cfg["mic"].get("name", "")
    devices = audio_mod.find_devices(hint)
    if devices == [None] and hint:
        print(f"[X] микрофон «{hint}» не найден. Список: run.ps1 mics")
        ok = False
    else:
        idx = devices[0]
        name = "по умолчанию" if idx is None else audio_mod.sd.query_devices(idx)["name"]
        print(f"[v] микрофон: {name} (запасных входов: {len(devices)-1})")

    print("[.] пробую записать 1 секунду...")
    try:
        rec = audio_mod.Recorder(devices, int(cfg["mic"]["samplerate"]))
        rec.start()
        time.sleep(1.0)
        data = rec.stop()
        peak, rms = audio_mod.loudness(data)
        dev, sr, ch = rec.recipe
        api = audio_mod.sd.query_hostapis()[
            audio_mod.sd.query_devices(dev)["hostapi"]
        ]["name"] if dev is not None else "по умолчанию"
        print(f"[v] открылся через {api}: {sr} Гц, {ch} кан.")
        print(f"[v] записалось {len(data)/16000:.2f} с, пик {peak:.4f}, громкость {rms:.4f}")
        if rms < SILENCE_RMS:
            print("    (тихо — это нормально, если ты молчал)")
    except Exception as exc:
        print(f"[X] запись не пошла: {exc}")
        ok = False

    print("[.] гружу распознавалку...")
    try:
        from .asr import Asr

        asr = Asr(cfg, cfg_mod.glossary())
        took = asr.load()
        print(f"[v] {asr.model_name} на {asr.device}, {took:.1f} с")
        warm = asr.warmup()
        print(f"[v] прогрев {warm:.2f} с")
        if asr.device != "cuda":
            print("[!] работает на процессоре — будет медленно")
            ok = False
    except Exception as exc:
        print(f"[X] распознавалка не поднялась: {exc}")
        ok = False

    pol = Polisher(cfg, cfg_mod.glossary())
    if pol.check():
        print(f"[v] причёсывание: LM Studio, модель {pol.model}")
    else:
        print(f"[!] причёсывания не будет: {pol.reason}")

    fx = Fixes(cfg_mod.FIXES_PATH)
    print(f"[v] словарь замен: {len(fx)} пар")

    print("\n" + ("ВСЁ НА МЕСТЕ" if ok else "ЕСТЬ ПРОБЛЕМЫ — смотри строки с [X]"))


def cmd_bench(path: str) -> None:
    from .asr import Asr

    cfg = cfg_mod.load()
    asr = Asr(cfg, cfg_mod.glossary())
    print(f"загрузка: {asr.load():.1f} с")
    print(f"прогрев:  {asr.warmup():.2f} с")

    import wave

    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    data = audio_mod._resample(pcm.astype(np.float32) / 32768.0, sr, 16000)
    print(f"файл: {len(data)/16000:.1f} с речи\n")

    for i in range(3):
        text, took = asr.transcribe(data)
        print(f"прогон {i+1}: {took:.2f} с")
    print(f"\nтекст: {text}")


SPOKENLY = Path(r"C:\Users\panto\AppData\Roaming\Spokenly\History")
RU_WORD_RE = __import__("re").compile(r"[а-яё]{3,}", __import__("re").IGNORECASE)


def cmd_learnwords(min_count: int = 1) -> None:
    """Собирает список русских слов, которые ты реально говоришь.

    Нужен, чтобы корректор не переводил твои слова в английские термины
    («сессию» -> «session»). Берёт всё, что уже надиктовано: историю Spokenly
    и журналы этой программы. Чем дольше пользуешься — тем список полнее.
    """
    import json
    from collections import Counter

    counts: Counter = Counter()
    sources = 0

    for jf in SPOKENLY.rglob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            ok = (data.get("content", {}).get("dictation", {}) or {}).get("success")
            text = (ok or {}).get("transcription_text") or ""
        except Exception:
            continue
        if text:
            sources += 1
            counts.update(w.lower() for w in RU_WORD_RE.findall(text))

    for lf in sorted(cfg_mod.LOG_DIR.glob("*.jsonl")):
        for line in lf.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            text = rec.get("final") or rec.get("raw") or ""
            if text:
                sources += 1
                counts.update(w.lower() for w in RU_WORD_RE.findall(text))

    keep = sorted(w for w, c in counts.items() if c >= min_count)
    header = [
        "# Русские слова, которые ты реально говоришь.",
        f"# Собрано из {sources} расшифровок, порог — {min_count} и больше повторов.",
        "# Корректору запрещено подменять эти слова английскими терминами.",
        "# Пересобрать: run.ps1 learnwords",
        "",
    ]
    cfg_mod.MYWORDS_PATH.write_text(
        "\n".join(header + keep) + "\n", encoding="utf-8"
    )
    print(f"расшифровок просмотрено: {sources}")
    print(f"разных русских слов:     {len(counts)}")
    print(f"попало в список:         {len(keep)} (встречались {min_count}+ раз)")
    print(f"файл:                    {cfg_mod.MYWORDS_PATH}")


def cmd_import_spokenly() -> None:
    """Забирает записи из Spokenly к себе — чтобы обучение не начиналось с нуля.

    Берём звук и расшифровку, которую сделал ElevenLabs (за него ты платишь,
    и на проверке он оказался хорош). Дальше это обычные записи: их видно на
    странице, можно послушать, поправить и пометить.
    """
    import json
    import wave

    src = SPOKENLY
    if not src.exists():
        print(f"нет папки {src}")
        return
    cfg_mod.ensure_dirs()

    existing = set()
    for lf in cfg_mod.LOG_DIR.glob("*.jsonl"):
        for line in lf.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("id"))
            except Exception:
                pass

    added = skipped = 0
    total_sec = 0.0
    by_day: dict[str, list] = {}

    for jf in sorted(src.rglob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            ok = (data.get("content", {}).get("dictation", {}) or {}).get("success")
        except Exception:
            continue
        if not ok:
            continue
        text = (ok.get("transcription_text") or "").strip()
        wav_src = jf.parent / (ok.get("audio_file_name") or "")
        if not text or not wav_src.exists():
            continue
        rec_id = f"spokenly_{data.get('id', jf.stem)}"
        if rec_id in existing:
            skipped += 1
            continue

        try:
            with wave.open(str(wav_src), "rb") as wf:
                sr, n_ch = wf.getframerate(), wf.getnchannels()
                pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        except Exception:
            continue
        data_f = pcm.astype(np.float32) / 32768.0
        if n_ch > 1:
            data_f = data_f.reshape(-1, n_ch).mean(axis=1)
        data_f = audio_mod._resample(data_f, sr, audio_mod.TARGET_SR)

        day = jf.parent.name
        stamp = rec_id.split("_", 1)[1][:12]
        folder = cfg_mod.REC_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        wav_dst = folder / f"{stamp}.wav"
        with wave.open(str(wav_dst), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(audio_mod.TARGET_SR)
            wf.writeframes((np.clip(data_f, -1, 1) * 32767).astype(np.int16).tobytes())
        (folder / f"{stamp}.txt").write_text(text, encoding="utf-8")

        secs = len(data_f) / audio_mod.TARGET_SR
        total_sec += secs
        by_day.setdefault(day, []).append(
            {
                "id": rec_id,
                "time": f"{day}T00:00:00",
                "seconds_audio": round(secs, 2),
                "raw": text,
                "after_fixes": text,
                "final": text,
                "polish_note": "перенесено из Spokenly (расшифровка ElevenLabs)",
                "ms_asr": 0,
                "ms_polish": 0,
                "ms_total": 0,
                "fixes_applied": 0,
                "learned": [],
                "wav": str(wav_dst),
                "source": "spokenly",
            }
        )
        added += 1

    for day, recs in by_day.items():
        with open(cfg_mod.LOG_DIR / f"{day}.jsonl", "a", encoding="utf-8") as fh:
            for rec in recs:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"перенесено:   {added} записей, {total_sec/60:.1f} мин речи")
    print(f"пропущено:    {skipped} (уже были)")
    print("Теперь они на странице: можно слушать, править и помечать.")
    print("Помеченные как плохие в дообучение не пойдут.")


def cmd_dry(paths: list[str]) -> None:
    """Прогон всей цепочки на готовых файлах — без микрофона и без вставки.

    Показывает каждую ступень отдельно: что услышала, что починил словарь,
    что сделал корректор, что откатил замок, и сколько на всё ушло.
    """
    import wave

    d = Dictation()
    from .asr import Asr

    d.asr = Asr(d.cfg, d.terms)
    print(f"загрузка распознавалки: {d.asr.load():.1f} с "
          f"({d.asr.model_name} на {d.asr.device})")
    print(f"прогрев: {d.asr.warmup():.2f} с")
    if d.polisher.check(force=True):
        print(f"корректор: {d.polisher.model} (подняли за {d.polisher.warmup():.1f} с)")
    else:
        print(f"корректор недоступен: {d.polisher.reason}")
    print(f"словарь: {len(d.fixes)} пар, терминов: {len(d.terms)}\n")

    totals = []
    for path in paths:
        with wave.open(path, "rb") as wf:
            sr, n_ch = wf.getframerate(), wf.getnchannels()
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        data = pcm.astype(np.float32) / 32768.0
        if n_ch > 1:
            data = data.reshape(-1, n_ch).mean(axis=1)
        data = audio_mod._resample(data, sr, audio_mod.TARGET_SR)

        t0 = time.perf_counter()
        raw, t_asr = d.asr.transcribe(audio_mod.normalize(data))
        pre, n_fix = d.fixes.apply(raw)
        polished, t_pol, note = d.polisher.polish(pre)
        final, _ = d.fixes.apply(polished)
        total = time.perf_counter() - t0
        totals.append((len(data) / audio_mod.TARGET_SR, total, t_asr, t_pol))

        print(f"--- {Path(path).name} ({len(data)/audio_mod.TARGET_SR:.1f} с речи) ---")
        print(f"услышала:  {raw}")
        if n_fix:
            print(f"словарь:   {pre}   [починил слов: {n_fix}]")
        if final != pre:
            print(f"корректор: {final}   [{note}]")
        elif note != "ок":
            print(f"корректор: без изменений   [{note}]")
        print(f"время:     {total:.2f} с  "
              f"(распознала {t_asr:.2f}, причесала {t_pol:.2f})\n")

    if totals:
        n = len(totals)
        print(f"=== по {n} записям ===")
        print(f"средняя длина речи: {sum(t[0] for t in totals)/n:.1f} с")
        print(f"среднее время:      {sum(t[1] for t in totals)/n:.2f} с")
        print(f"худшее время:       {max(t[1] for t in totals):.2f} с")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    if cmd == "mics":
        cmd_mics()
    elif cmd == "keytest":
        cmd_keytest(int(args[1]) if len(args) > 1 else 12)
    elif cmd == "mousetest":
        from . import mousehook

        mousehook.watch(int(args[1]) if len(args) > 1 else 12)
    elif cmd == "selftest":
        cmd_selftest()
    elif cmd == "bench":
        cmd_bench(args[1])
    elif cmd == "dry":
        cmd_dry(args[1:])
    elif cmd == "learnwords":
        cmd_learnwords(int(args[1]) if len(args) > 1 else 1)
    elif cmd == "import-spokenly":
        cmd_import_spokenly()
    elif cmd == "dashboard":
        import webbrowser

        cfg = cfg_mod.load()
        url = f"http://127.0.0.1:{cfg.get('web', {}).get('port', 8756)}/"
        print(f"открываю {url}")
        print("(страницу поднимает сама диктовка — она должна быть запущена)")
        webbrowser.open(url)
    else:
        Dictation().run()


if __name__ == "__main__":
    main()
