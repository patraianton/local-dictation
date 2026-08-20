# -*- coding: utf-8 -*-
"""Dictation: hold the key, speak, the text lands in the window.

Usage:
    run.ps1              — run it
    run.ps1 mics         — list the microphones
    run.ps1 keytest      — find the scan code of a key
    run.ps1 selftest     — check everything is in place
    run.ps1 bench FILE   — how long recognition takes
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

    # ---------- startup ----------
    def boot(self) -> None:
        from .asr import Asr

        self.hud.set("think", "loading")
        mic_name = "default"
        if self.device is not None:
            mic_name = audio_mod.sd.query_devices(self.device)["name"]
        log(f"microphone: {mic_name}")

        self.asr = Asr(self.cfg, self.terms)
        took = self.asr.load()
        log(f"recognizer {self.asr.model_name} on {self.asr.device}: {took:.1f} s")
        warm = self.asr.warmup()
        log(f"warmup: {warm:.2f} s")

        # When LM Studio is started later, the background probe notices it and
        # says so — otherwise the corrector would quietly come back and Anton
        # would have no idea when.
        def corrector_back(_ok: bool, model: str) -> None:
            log(f"corrector is back: {model}")
            self.hud.set("ok", "corrector on", hide_after=2.0)

        self.polisher.on_status = corrector_back

        if self.polisher.check():
            warm = self.polisher.warmup()
            log(f"corrector: {self.polisher.model} (loaded in {warm:.1f} s)")
        else:
            log(f"corrector OFF — {self.polisher.reason}")
            log("  (dictation works without it; the text comes out raw)")

        log(f"replacements: {len(self.fixes)} pairs, terms in the hint: {len(self.terms)}")
        web = self.cfg.get("web", {})
        if web.get("enabled", True):
            from . import server

            try:
                url = server.start(
                    int(web.get("port", 8756)), self.fixes, self.reload_terms,
                    self.polisher,
                )
                log(f"dictation page: {url}")
                if web.get("open_on_start", False):
                    import webbrowser

                    webbrowser.open(url)
            except Exception as exc:
                log(f"the page did not start: {exc}")

        # If the app was killed mid-recording last time, other apps are still
        # ducked — give their volume back.
        self.ducker.recover()
        if self.ducker.enabled:
            log(f"other audio while recording: down to {self.ducker.level*100:.0f}%")

        self.bind_keys()
        threading.Thread(target=self._keep_warm, daemon=True).start()
        self.hud.set("ok", "ready", hide_after=1.5)
        log(f"READY. Key {self.key}: hold = speak, tap = hands-free.")
        log(f"Fix the last take: {self.fix_hotkey}. Cancel recording: Esc.")
        if self.flip_hotkey:
            log(f"Full stop <-> question mark: {self.flip_hotkey}.")

    def reload_terms(self) -> None:
        """Re-reads the terms live, with no restart.

        A word added on the page has to work from the very next take: both in
        the hint to the recognizer and in the corrector.
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
        log(f"terms reloaded: {len(self.terms)}")

    def _keep_warm(self) -> None:
        """Keeps the corrector resident in VRAM.

        LM Studio unloads a model after a while idle, and the first take after
        a break then waits for it (measured: 2.1 s instead of 0.2). We quietly
        ping it every 10 minutes.
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
            log(f"the fix key {self.fix_hotkey} did not bind: {exc}")
        if self.flip_hotkey:
            try:
                keyboard.add_hotkey(
                    self.flip_hotkey, self.on_flip_question, suppress=False
                )
            except Exception as exc:
                log(f"the mark key {self.flip_hotkey} did not bind: {exc}")

        rp = self.cfg.get("repaste", {})
        if not rp.get("enabled", True):
            return
        if rp.get("key"):
            try:
                keyboard.add_hotkey(rp["key"], self.on_repaste, suppress=False)
                log(f"paste again: key {rp['key']}")
            except Exception as exc:
                log(f"key {rp['key']} did not bind: {exc}")
        if rp.get("button"):
            from . import mousehook

            self.mouse_hook = mousehook.Hook(
                rp["button"], self.on_repaste, bool(rp.get("suppress", False))
            )
            try:
                if self.mouse_hook.start():
                    mode = "intercepted" if rp.get("suppress", False) else "not intercepted"
                    log(f"paste again: side mouse buttons "
                        f"({self.mouse_hook.names}, {mode})")
                else:
                    log(f"mouse buttons {rp['button']!r} not recognized")
            except Exception as exc:
                log(f"the mouse hook failed: {exc}")

    # ---------- the key ----------
    def on_down(self, _event=None) -> None:
        if self.recording:
            if self.locked:
                self.stop_and_process()
            return  # key auto-repeat
        self.start()

    def on_up(self, _event=None) -> None:
        if not self.recording or self.locked:
            return
        held_ms = (time.perf_counter() - self.t_down) * 1000
        if held_ms < self.tap_ms:
            self.locked = True
            self.hud.set("lock", "hands-free")
        else:
            self.stop_and_process()

    def on_esc(self, _event=None) -> None:
        if self.recording:
            self.recording = self.locked = False
            self.recorder.stop()
            self.ducker.restore()
            self.hud.set("warn", "cancelled", hide_after=1.0)
            log("recording cancelled")

    def on_repaste(self) -> None:
        """Paste the last take into the window under the mouse.

        For the "the text went to the wrong place" case: point at the right
        window and press the button. The app brings it forward and pastes.
        """
        text = (self.last or {}).get("final", "")
        if not text:
            self.hud.set("warn", "nothing to paste", hide_after=1.2)
            return
        from . import mousehook

        hwnd = mousehook.window_under_cursor()
        title = mousehook.window_title(hwnd)
        if not mousehook.focus(hwnd):
            self.hud.set("err", "window refused focus", hide_after=2.0)
            log(f"could not bring the window forward: {title!r}")
            return
        time.sleep(0.06)  # the window needs a moment to accept focus
        paste_text(
            text,
            self.cfg["paste"].get("hotkey", "ctrl+v"),
            float(self.cfg["paste"].get("restore_clipboard_after_s", 1.0)),
        )
        self.hud.set("ok", "pasted", hide_after=1.0)
        log(f"pasted again into window: {title[:60]!r}")

    def on_flip_question(self) -> None:
        """Flips the final mark of the last take: full stop <-> question mark.

        Why this is manual. "Скоро это уже закончится" and "Скоро это уже
        закончится?" are the same words; only the voice differs. Measured
        2026-08-14: the voice carries no usable signal (2 out of 117 on real
        takes), and both feeding previous lines as context and asking the model
        directly made things worse. So the last word is the human's — but with
        one key, not through the edit window.

        It also fixes what was already pasted: erases the last mark and types
        the right one. The cursor must sit right after the pasted text, which is
        the case if you press it straight after dictating.
        """
        text = (self.last or {}).get("final", "")
        if not text.strip():
            self.hud.set("warn", "nothing to fix", hide_after=1.2)
            return

        import keyboard

        from .polish import flip_question

        new_text, erase, want = flip_question(text)
        self.last["final"] = new_text

        # fix the already pasted text: erase the wrong mark, type the right one
        try:
            for _ in range(erase):
                keyboard.send("backspace")
                time.sleep(0.02)
            keyboard.write(want)
        except Exception as exc:
            log(f"could not fix it in the window: {exc}")

        # remember the correction: it goes to the page and to the training data
        rec_id = (self.last or {}).get("id")
        if rec_id:
            try:
                from . import store

                res = store.set_text(rec_id, self.last["final"], self.fixes)
                if res.get("learned"):
                    self.fixes.load()
            except Exception as exc:
                log(f"the correction was not saved: {exc}")

        self.hud.set("ok", f"set {want!r}", hide_after=1.2)
        log(f"final mark -> {want!r}")
        log(f"  {self.last['final']}")

    def on_fix(self) -> None:
        if not self.last or self.hud.root is None:
            self.hud.set("warn", "nothing to fix", hide_after=1.2)
            return
        from .fixwin import open_window

        def done(learned: int):
            self.hud.set("ok", f"learned: {learned}", hide_after=1.8)
            log(f"pairs learned: {learned} (total {len(self.fixes)})")

        self.hud.root.after(
            0, lambda: open_window(self.hud.root, self.last, self.fixes, done)
        )

    # ---------- recording ----------
    def start(self) -> None:
        # A held key fires auto-repeat. If the mic failed to open, without a
        # pause we would get a hundred identical attempts per second.
        if time.perf_counter() < self.mic_cooldown:
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.mic_cooldown = time.perf_counter() + 3.0
            self.hud.set("err", "mic did not open", hide_after=3.0)
            log(f"the microphone did not open: {exc}")
            return
        self.recording, self.locked = True, False
        self.t_down = time.perf_counter()
        self.ducker.duck()      # turn other audio down while you speak
        self.hud.set("rec", "")
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self) -> None:
        started = time.perf_counter()
        while self.recording:
            if time.perf_counter() - started > self.max_seconds:
                log("the maximum-length safety net fired")
                self.stop_and_process()
                return
            time.sleep(0.25)

    def stop_and_process(self) -> None:
        if not self.recording:
            return
        self.recording = self.locked = False
        self.hud.set("think", "thinking")
        # Stop on another thread: recording the tail waits a fraction of a
        # second, and on the keyboard thread that wait would block every other
        # key.
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        data = self.recorder.stop(
            tail_s=self.tail_s, quiet_ms=self.tail_quiet_ms
        )
        self.ducker.restore()
        self.process(data)

    # ---------- processing ----------
    def transcribe_resilient(self, audio: np.ndarray) -> tuple[str, float]:
        """Recognizes; if the GPU fell away, brings the model back up.

        Why. On 2026-08-19 the machine slept, the GPU context died, and the app
        spent four days answering "FAILED" to every take. It could not get out
        of that on its own: the in-memory model was dead and nothing was going
        to reload it.
        """
        try:
            return self.asr.transcribe(audio)
        except Exception as exc:
            if not self.asr.looks_like_lost_gpu(exc):
                raise
            log(f"the GPU fell away ({type(exc).__name__}), reloading the model")
            self.hud.set("think", "reloading")
            where = self.asr.reload()
            if where == "cpu":
                log("failed — switched to the CPU. This will be slower.")
                log(r"A restart fixes it: .\start-background.ps1 -Restart")
            else:
                log(f"the model is back on {where}")
            return self.asr.transcribe(audio)

    def process(self, data: np.ndarray) -> None:
        if not self.busy.acquire(blocking=False):
            log("the previous take is still being processed — skipping")
            return
        try:
            t_all = time.perf_counter()
            secs = len(data) / audio_mod.TARGET_SR
            peak, rms = audio_mod.loudness(data)
            if secs < MIN_SECONDS:
                self.hud.set("warn", "too short", hide_after=1.2)
                return
            if rms < SILENCE_RMS:
                self.hud.set("warn", "silence on the mic", hide_after=2.0)
                log(f"silence: {secs:.1f} s, peak {peak:.4f}")
                return

            raw, t_asr = self.transcribe_resilient(audio_mod.normalize(data))
            if not raw.strip():
                self.hud.set("warn", "nothing recognized", hide_after=1.5)
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
            self.hud.set("ok", f"{total:.1f} s", hide_after=1.2)
            log(f"{secs:.1f} s of speech -> {total:.2f} s "
                f"(recognized {t_asr:.2f}, corrected {t_pol:.2f}, {note})")
            log(f"  {final}")
            for src, dst in flipped:
                log(f"  restored the order: {src!r} -> {dst!r}")
            for src, dst in promoted:
                log(f"  learned: {src!r} -> {dst!r}")
        except Exception as exc:
            self.hud.set("err", "failed", hide_after=2.5)
            log(f"FAILED: {type(exc).__name__}: {exc}")
        finally:
            self.busy.release()

    def run(self) -> None:
        threading.Thread(target=self.boot, daemon=True).start()
        try:
            self.hud.run()
        except KeyboardInterrupt:
            pass


# ---------- helper commands ----------
def cmd_mics() -> None:
    for d in audio_mod.list_inputs():
        print(f"{d['index']:>3}  {d['hostapi']:<20} {d['name']}  "
              f"({d['default_samplerate']} Hz, {d['channels']} ch)")


def cmd_keytest(seconds: int = 12) -> None:
    """Catches key presses for a few seconds and says whether the wanted key arrives."""
    import keyboard

    cfg = cfg_mod.load()
    want = cfg["hotkey"].get("name", "f13")
    try:
        want_codes = set(keyboard.key_to_scan_codes(want))
    except Exception:
        want_codes = set()

    print(f"Press the key you want for dictation. Listening for {seconds} s.")
    print(f"(the settings currently say {want!r})\n")

    pressed: dict = {}

    def show(e):
        if e.event_type != "down":
            return
        key = (e.name, e.scan_code)
        if key in pressed:
            return
        pressed[key] = True
        mark = "  <-- THIS IS THE ONE IN THE SETTINGS" if e.scan_code in want_codes else ""
        print(f"  name: {str(e.name)!r:<14} scan code: {e.scan_code}{mark}")

    keyboard.hook(show)
    time.sleep(seconds)
    keyboard.unhook_all()

    print()
    if not pressed:
        print("No key presses caught.")
        return
    hit = [k for k in pressed if k[1] in want_codes]
    if hit:
        print(f"GOOD: the key {want!r} arrives, nothing to change.")
    else:
        names = ", ".join(f"{k[0]} (code {k[1]})" for k in pressed)
        print(f"The key {want!r} did NOT arrive. Caught these instead: {names}")
        print("Put the right one into config.toml -> [hotkey] name or scancode.")


def cmd_selftest() -> None:
    ok = True
    cfg = cfg_mod.load()
    print("=== self-test ===\n")

    hint = cfg["mic"].get("name", "")
    devices = audio_mod.find_devices(hint)
    if devices == [None] and hint:
        print(f"[X] microphone {hint!r} not found. List them: run.ps1 mics")
        ok = False
    else:
        idx = devices[0]
        name = "default" if idx is None else audio_mod.sd.query_devices(idx)["name"]
        print(f"[v] microphone: {name} (fallback inputs: {len(devices)-1})")

    print("[.] trying to record 1 second...")
    try:
        rec = audio_mod.Recorder(devices, int(cfg["mic"]["samplerate"]))
        rec.start()
        time.sleep(1.0)
        data = rec.stop()
        peak, rms = audio_mod.loudness(data)
        dev, sr, ch = rec.recipe
        api = audio_mod.sd.query_hostapis()[
            audio_mod.sd.query_devices(dev)["hostapi"]
        ]["name"] if dev is not None else "default"
        print(f"[v] opened via {api}: {sr} Hz, {ch} ch")
        print(f"[v] recorded {len(data)/16000:.2f} s, peak {peak:.4f}, level {rms:.4f}")
        if rms < SILENCE_RMS:
            print("    (quiet is fine if you said nothing)")
    except Exception as exc:
        print(f"[X] recording failed: {exc}")
        ok = False

    print("[.] loading the recognizer...")
    try:
        from .asr import Asr

        asr = Asr(cfg, cfg_mod.glossary())
        took = asr.load()
        print(f"[v] {asr.model_name} on {asr.device}, {took:.1f} s")
        warm = asr.warmup()
        print(f"[v] warmup {warm:.2f} s")
        if asr.device != "cuda":
            print("[!] running on the CPU — this will be slow")
            ok = False
    except Exception as exc:
        print(f"[X] the recognizer did not start: {exc}")
        ok = False

    pol = Polisher(cfg, cfg_mod.glossary())
    if pol.check():
        print(f"[v] corrector: LM Studio, model {pol.model}")
    else:
        print(f"[!] no corrector: {pol.reason}")

    fx = Fixes(cfg_mod.FIXES_PATH)
    print(f"[v] replacements: {len(fx)} pairs")

    print("\n" + ("ALL GOOD" if ok else "PROBLEMS — see the [X] lines"))


def cmd_bench(path: str) -> None:
    from .asr import Asr

    cfg = cfg_mod.load()
    asr = Asr(cfg, cfg_mod.glossary())
    print(f"load:   {asr.load():.1f} s")
    print(f"warmup: {asr.warmup():.2f} s")

    import wave

    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    data = audio_mod._resample(pcm.astype(np.float32) / 32768.0, sr, 16000)
    print(f"file: {len(data)/16000:.1f} s of speech\n")

    for i in range(3):
        text, took = asr.transcribe(data)
        print(f"run {i+1}: {took:.2f} s")
    print(f"\ntext: {text}")


SPOKENLY = Path(r"C:\Users\panto\AppData\Roaming\Spokenly\History")
RU_WORD_RE = __import__("re").compile(r"[а-яё]{3,}", __import__("re").IGNORECASE)


def cmd_learnwords(min_count: int = 1) -> None:
    """Builds the list of your own-language words that you actually say.

    It stops the corrector from turning your words into English terms
    ("сессию" -> "session"). Built from everything already dictated: the
    Spokenly history and this app's own logs. The longer you use it, the fuller
    the list.
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
        "# Words of your own language that you actually say.",
        f"# Built from {sources} transcripts, threshold: seen {min_count}+ times.",
        "# The corrector may not swap these for English terms.",
        "# Rebuild with: run.ps1 learnwords",
        "",
    ]
    cfg_mod.MYWORDS_PATH.write_text(
        "\n".join(header + keep) + "\n", encoding="utf-8"
    )
    print(f"transcripts scanned:  {sources}")
    print(f"distinct words:       {len(counts)}")
    print(f"kept in the list:     {len(keep)} (seen {min_count}+ times)")
    print(f"file:                 {cfg_mod.MYWORDS_PATH}")


def cmd_import_spokenly() -> None:
    """Imports takes from Spokenly so training does not start from zero.

    Takes the audio and the transcript ElevenLabs produced (you pay for it, and
    on inspection it turned out to be good). From then on they are ordinary
    takes: visible on the page, playable, editable, markable.
    """
    import json
    import wave

    src = SPOKENLY
    if not src.exists():
        print(f"no such folder: {src}")
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
                "polish_note": "imported from Spokenly (ElevenLabs transcript)",
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

    print(f"imported: {added} takes, {total_sec/60:.1f} min of speech")
    print(f"skipped:  {skipped} (already there)")
    print("They are on the page now: listen, edit and mark them.")
    print("Ones marked bad will be left out of training.")


def cmd_dry(paths: list[str]) -> None:
    """Runs the whole chain over existing files — no microphone, no pasting.

    Shows every stage separately: what was heard, what the dictionary fixed,
    what the corrector did, what the lock rolled back, and how long it all took.
    """
    import wave

    d = Dictation()
    from .asr import Asr

    d.asr = Asr(d.cfg, d.terms)
    print(f"loading the recognizer: {d.asr.load():.1f} s "
          f"({d.asr.model_name} on {d.asr.device})")
    print(f"warmup: {d.asr.warmup():.2f} s")
    if d.polisher.check(force=True):
        print(f"corrector: {d.polisher.model} (loaded in {d.polisher.warmup():.1f} s)")
    else:
        print(f"corrector unavailable: {d.polisher.reason}")
    print(f"dictionary: {len(d.fixes)} pairs, terms: {len(d.terms)}\n")

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

        print(f"--- {Path(path).name} ({len(data)/audio_mod.TARGET_SR:.1f} s of speech) ---")
        print(f"heard:     {raw}")
        if n_fix:
            print(f"dictionary:{pre}   [words fixed: {n_fix}]")
        if final != pre:
            print(f"corrector: {final}   [{note}]")
        elif note != "ok":
            print(f"corrector: unchanged   [{note}]")
        print(f"time:      {total:.2f} s  "
              f"(recognized {t_asr:.2f}, corrected {t_pol:.2f})\n")

    if totals:
        n = len(totals)
        print(f"=== over {n} takes ===")
        print(f"average speech length: {sum(t[0] for t in totals)/n:.1f} s")
        print(f"average time:         {sum(t[1] for t in totals)/n:.2f} s")
        print(f"worst time:           {max(t[1] for t in totals):.2f} s")


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
        print(f"opening {url}")
        print("(the page is served by the app itself — it has to be running)")
        webbrowser.open(url)
    else:
        Dictation().run()


if __name__ == "__main__":
    main()
