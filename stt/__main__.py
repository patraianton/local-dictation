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
from . import miclisteners as listeners_mod  # noqa: E402
from . import micgain as micgain_mod  # noqa: E402
from . import termstats  # noqa: E402
from . import paste as paste_mod  # noqa: E402
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
        # The order the recognizer's hint is cut from: what is actually being
        # talked about, not the order the file happens to be in. See
        # termstats.py — the whole list still goes to the corrector.
        self.hint_terms = self._rank_terms()
        self.fix_endings = bool(self.cfg.get("endings", {}).get("enabled", True))
        self.polisher = Polisher(self.cfg, self.terms, self.fixes, self.mywords)
        self.learner = Learner(
            self.cfg, self.fixes, cfg_mod.LOG_DIR, cfg_mod.REC_DIR,
            cfg_mod.CANDIDATES_PATH, self.terms,
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

        self.mic_path = str(mic_cfg.get("path", "raw"))
        self.devices = audio_mod.find_devices(
            self.cfg["mic"].get("name", ""), self.mic_path
        )
        self.device = self.devices[0]
        self.preroll_s = float(mic_cfg.get("preroll_ms", 500)) / 1000.0
        mic_name = self.cfg["mic"].get("name", "")
        self.recorder = audio_mod.Recorder(
            self.devices,
            int(self.cfg["mic"]["samplerate"]),
            preroll_s=self.preroll_s,
            hot_s=float(mic_cfg.get("hot_ms", 20000)) / 1000.0,
            # Re-plug the microphone and it is found again by name, not by
            # the index it had before (04.09.2026).
            finder=lambda: audio_mod.find_devices(mic_name, self.mic_path),
            named=bool(mic_name.strip()),
        )
        self.recorder.log = log
        self.gain = micgain_mod.MicGain(
            self.cfg["mic"].get("name", ""), self.cfg,
            cfg_mod.MICGAIN_STATE_PATH, log,
        )
        # Putting back a question mark from the voice — see ask_the_sound.
        pol_cfg = self.cfg.get("polish", {})
        self.sound_questions = bool(pol_cfg.get("sound_questions", True))
        self.sound_threshold = float(pol_cfg.get("sound_threshold", -1.0))
        self.last_sound_q = None
        self.mic_cooldown = 0.0
        # Which window the last take went into. Kept for the paste path only.
        self.hot_window = ""
        # Programs that get the microphone handed back the moment their window
        # comes to the front. Everything else may wait: holding the mic open is
        # what keeps the pre-roll full, and the pre-roll is what saves the first
        # syllable of a phrase.
        self.yield_to = {
            str(x).strip().lower()
            for x in mic_cfg.get("yield_to", []) if str(x).strip()
        }
        # How long a program from that list is given to actually start
        # recording once its window comes to the front, and how often that
        # chance comes round again while it stays in front. See _may_hold_mic.
        self.yield_probe_s = float(mic_cfg.get("yield_probe_s", 2.5))
        self.yield_probe_every_s = float(mic_cfg.get("yield_probe_every_s", 25.0))
        # A program that really does record on this machine gets its chance far
        # more often, so a call never waits half a minute to start.
        self.yield_probe_hot_every_s = float(
            mic_cfg.get("yield_probe_hot_every_s", 4.0))
        self.yield_known_s = float(mic_cfg.get("yield_known_s", 86400.0))
        # A call is still a call across a mute or a screen share, both of which
        # close the capture stream for a moment.
        self.yield_grace_s = float(mic_cfg.get("yield_grace_s", 120.0))
        self._front_exe = ""
        self._front_since = 0.0
        self._probe_until = 0.0
        self.recorder.should_hold = self._may_hold_mic

        self.asr = None
        self.recording = False
        self.locked = False
        self.t_down = 0.0
        self.busy = threading.Lock()
        self.last: dict = {}
        self.mouse_hook = None
        self.toggle_hook = None

    # ---------- startup ----------
    def boot(self) -> None:
        from .asr import Asr

        self.hud.set("think", "loading")
        mic_name = "default"
        if self.device is not None:
            mic_name = audio_mod.sd.query_devices(self.device)["name"]
        log(f"microphone: {mic_name} [{audio_mod.api_of(self.device)}]")

        self.gain.open()
        log(micgain_mod.describe(self.gain))
        self.gain.start()

        if self.recorder.warm():
            log(f"microphone held open, {self.recorder.hot_s:.0f} s after a take "
                f"(the first {self.preroll_s*1000:.0f} ms of a phrase are kept)")
        elif self.recorder.hot_s > 0:
            log(f"could not hold the microphone open: {self.recorder.last_error}")

        self.asr = Asr(self.cfg, self.hint_terms)
        took = self.asr.load()
        log(f"recognizer {self.asr.model_name} on {self.asr.device}: {took:.1f} s")
        if self.asr.short is not None:
            log(f"short takes (up to {self.asr.short_seconds:.0f} s) go to "
                f"{self.asr.short_model_name} — it hears a one-word command better")
        elif self.asr.short_model_name:
            log(f"the second model {self.asr.short_model_name} did not load — "
                f"short takes go to {self.asr.model_name} as before")
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

        hint_size = int(self.cfg.get("asr", {}).get("prompt_terms", 45))
        log(f"replacements: {len(self.fixes)} pairs, "
            f"{len(self.terms)} terms known, {min(hint_size, len(self.hint_terms))} "
            f"of them in the recognizer's hint")
        log("  hint starts with: " + ", ".join(self.hint_terms[:10]))
        # A pair the dictionary would refuse today but that is sitting in the
        # file anyway: it was hand-written, or it survives from an older
        # version. It still works — load() does not filter — so the only way it
        # is ever noticed is here. Right now there are none, which is what makes
        # any future line worth reading.
        suspect = self.fixes.suspect_pairs()
        for src, dst, why in suspect:
            log(f"  WATCH: replacement {src!r} -> {dst!r} would be refused today ({why})")
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

    def _rank_terms(self) -> list[str]:
        """The glossary in "how often it is really said" order.

        Never fatal: a missing log folder, an unreadable file, anything at all
        and the file's own order is used, exactly as before.
        """
        try:
            return termstats.rank(
                self.terms, cfg_mod.LOG_DIR,
                aliases=termstats.aliases_from_fixes(self.fixes),
                days=int(self.cfg.get("asr", {}).get("prompt_days", 14)),
                # A term that is also an ordinary Russian word cannot be
                # counted: "Это" is a colleague and the word "this" at once.
                skip=self.mywords,
            )
        except Exception as exc:
            log(f"could not rank the terms ({exc}) — using the file order")
            return list(self.terms)

    def reload_terms(self) -> None:
        """Re-reads the terms live, with no restart.

        A word added on the page has to work from the very next take: both in
        the hint to the recognizer and in the corrector.
        """
        from .asr import build_prompt
        from .polish import allowed_words

        self.terms = cfg_mod.glossary()
        self.mywords = cfg_mod.mywords()
        self.hint_terms = self._rank_terms()
        if self.asr is not None:
            a = self.cfg["asr"]
            self.asr.prompt = build_prompt(
                self.hint_terms, int(a.get("prompt_terms", 45)),
                a.get("prompt_style", "list"),
            )
        self.polisher.terms = self.terms
        self.polisher.allowed = allowed_words(self.terms, self.fixes)
        self.polisher.protected = self.mywords
        self.learner.terms = {t.strip().lower() for t in self.terms if t.strip()}
        log(f"terms reloaded: {len(self.terms)}; "
            f"hint: {', '.join(self.hint_terms[:6])}...")

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

        hf = self.cfg.get("handsfree", {})
        if hf.get("enabled", True) and (hf.get("key") or hf.get("button")):
            if hf.get("key"):
                try:
                    # Swallowed when asked, because the key a mouse button sends
                    # usually still has its own job in Windows. Print Screen is
                    # the case that made this necessary: unswallowed, every
                    # start of a recording also opened the Snipping Tool.
                    quiet = bool(hf.get("suppress", False))
                    keyboard.add_hotkey(hf["key"], self.on_toggle, suppress=quiet)
                    mode = "intercepted" if quiet else "not intercepted"
                    log(f"start/stop without holding: key {hf['key']} ({mode})")
                except Exception as exc:
                    log(f"key {hf['key']} did not bind: {exc}")
            if hf.get("button"):
                from . import mousehook

                self.toggle_hook = mousehook.Hook(
                    hf["button"], self.on_toggle, bool(hf.get("suppress", False))
                )
                try:
                    if self.toggle_hook.start():
                        log(f"start/stop without holding: mouse "
                            f"({self.toggle_hook.names})")
                    else:
                        log(f"mouse buttons {hf['button']!r} not recognized")
                except Exception as exc:
                    log(f"the mouse hook failed: {exc}")

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

    def on_toggle(self, _event=None) -> None:
        """One press starts recording, the next stops it. No holding anything.

        The same hands-free mode the main key gives on a short tap, but on its
        own button — a mouse button is pressed and released in a few
        milliseconds, so "hold to speak" is not a thing there.
        """
        if self.recording:
            self.stop_and_process()
            return
        self.start()
        self.locked = True
        self.hud.set("lock", "hands-free")

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
        log(f"pasted again into window: {title[:60]!r} ({paste_mod.last_route})")

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

        from .polish import flip_question

        new_text, erase, want = flip_question(text)
        self.last["final"] = new_text

        # fix the already pasted text: erase the wrong mark, type the right one
        try:
            paste_mod.erase_and_type(erase, want)
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

    def _may_hold_mic(self) -> bool:
        """Whether to keep holding the microphone between takes.

        Until 2026-08-29 this asked a different question: "is Anton still in
        the window the last take was pasted into?" — and let the microphone go
        the moment he was not. He almost never is: you dictate into one window
        and walk to the next. So the stream was closed within 250 ms of nearly
        every take, and the next key press opened it from cold — which costs
        about 105 ms of dead air AND starts with an empty pre-roll ring, so the
        half-second before the key press was simply not there.

        Measured over 2206 takes from 20.08 to 29.08.2026: 68% of them carried
        no pre-roll at all (the figure is bimodal — either the full 0.5 s or
        exactly zero, nothing between), and among those the take had to be
        re-dictated or was marked bad three times as often: 2.19% against 0.72%.
        Even with less than a minute since the previous take — when the timer
        still had the stream open — 47% arrived with nothing, because this veto
        had already closed it.

        Now the microphone is only handed back to programs that actually want
        it, listed in [mic] yield_to. Note that browsers are deliberately NOT
        on that list: dictation goes into a browser all day, and putting one
        there would bring back the empty pre-roll. A call inside a browser tab
        therefore needs the dictation key pressed once (which frees the mic on
        release) or chrome.exe added to yield_to by hand.

        And since 31.08.2026 being in that list is no longer enough: the
        program must be recording, or plausibly about to. Anton keeps Slack and
        Telegram in front of him all day and dictates into them — and they were
        taking the microphone away every time, purely for standing in front.
        Measured over 31.08: Slack had not recorded a single second since
        13.08, Telegram one minute at 09:51, yet 36 of the day's takes (21%)
        came in less than two minutes after the previous one and STILL had an
        empty pre-roll, i.e. the mic had been handed to a program that did not
        want it and the first syllable was gone.

        Our capture is exclusive (WDM-KS, measured 31.08: while dictation holds
        the microphone another program gets "Device unavailable"), so a program
        cannot simply take the microphone when it needs it — it has to be given
        a gap. Hence the probe: whenever a listed program comes to the front it
        gets `yield_probe_s` with the microphone free, and while it stays in
        front that chance comes round every `yield_probe_every_s`. Start a call
        and Windows marks the program as recording (see miclisteners.py), the
        microphone stays free for as long as the call lasts, plus
        `yield_grace_s` to survive a mute or a screen share. Do nothing with it
        and dictation takes the microphone straight back, pre-roll and all.
        """
        if not self.yield_to:
            return True
        now = (paste_mod.foreground_exe() or "").lower()
        if now not in self.yield_to:
            self._front_exe = now
            return True
        clock = time.perf_counter()
        # Somebody in the list is in front. Is it actually using the mic?
        try:
            if listeners_mod.is_recording(now, within_s=self.yield_grace_s):
                self._front_exe = now
                self._front_since = clock
                self._probe_until = clock + self.yield_probe_s
                return False
        except Exception:
            # No registry, no answer — fall back to the old behaviour and let
            # the microphone go, because a broken call is worse than a lost
            # syllable.
            return False
        if now != self._front_exe:
            # It has just come to the front: give it the microphone for a
            # moment, in case a call is being started right now.
            self._front_exe = now
            self._front_since = clock
            self._probe_until = clock + self.yield_probe_s
            return False
        if clock < self._probe_until:
            return False
        # How often the chance comes round depends on whether this program has
        # ever really used the microphone on this machine. Zoom did (a call on
        # 31.08 at 19:01); Slack has not since 13.08 and Telegram for one
        # minute all day. A call has to be able to start within a few seconds,
        # and a chat window has no business costing the pre-roll every half
        # minute either.
        every = self.yield_probe_every_s
        try:
            if listeners_mod.is_recording(now, within_s=self.yield_known_s):
                every = self.yield_probe_hot_every_s
        except Exception:
            pass
        if clock - self._probe_until >= every:
            self._probe_until = clock + self.yield_probe_s
            return False
        return True

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
        self.hot_window = paste_mod.foreground_exe()
        data = self.recorder.stop(
            tail_s=self.tail_s, quiet_ms=self.tail_quiet_ms
        )
        self.ducker.restore()
        self.process(data)

    # ---------- processing ----------
    def transcribe_resilient(self, audio: np.ndarray,
                             speech_s: float | None = None) -> tuple[str, float]:
        """Recognizes; if the GPU fell away, brings the model back up.

        Why. On 2026-08-19 the machine slept, the GPU context died, and the app
        spent four days answering "FAILED" to every take. It could not get out
        of that on its own: the in-memory model was dead and nothing was going
        to reload it.
        """
        try:
            return self.asr.transcribe(audio, speech_s)
        except Exception as exc:
            if not self.asr.looks_like_lost_gpu(exc):
                raise
            # Out of video memory is not the same as a dead GPU: something big
            # was loaded next to us (LM Studio holds the owner's model, and it
            # can be thirty gigabytes). Give up our own extra first and try
            # again — short takes get worse, dictation keeps working.
            if "memory" in f"{exc}".lower() and self.asr.drop_short():
                log("the video card ran out of memory — the second model for "
                    "short takes is given up, dictation continues")
                self.hud.set("warn", "gave up the extra model", hide_after=3.0)
                try:
                    return self.asr.transcribe(audio, speech_s)
                except Exception as again:
                    if not self.asr.looks_like_lost_gpu(again):
                        raise
                    exc = again
            log(f"the GPU fell away ({type(exc).__name__}), reloading the model")
            self.hud.set("think", "reloading")
            where = self.asr.reload()
            if where == "cpu":
                log("failed — switched to the CPU. This will be slower.")
                log(r"A restart fixes it: .\start-background.ps1 -Restart")
            else:
                log(f"the model is back on {where}")
            return self.asr.transcribe(audio, speech_s)

    def ask_the_sound(self, data: np.ndarray, raw: str, final: str):
        """A question mark the recognizer missed, put back from the voice.

        Runs only when nothing else found a question: neither the recognizer
        nor the corrector. Then the recognizer is asked once more, differently
        — which of the two endings, "." or "?", fits the recording better (see
        Asr.question_score). Above the threshold the mark goes in.

        Measured on 421 takes from 08-10.09.2026 (148 of them real questions):
        the app delivered 124 of those; with this, 129, and not one extra mark
        on a statement. It is the only thing here that can hear a question with
        no question word in it, and 14 of the 24 lost ones were exactly that.

        Returns the new text, or None to leave everything as it was.
        """
        self.last_sound_q = None
        if not self.sound_questions or "?" in final or not final.strip():
            return None
        t0 = time.perf_counter()
        d = self.asr.question_score(data, raw)
        took = time.perf_counter() - t0
        self.last_sound_q = d
        if d is None or d <= self.sound_threshold:
            return None
        from .polish import flip_question

        new_text, _erase, _want = flip_question(final)
        log(f"  the voice asked a question (score {d:+.1f} > "
            f"{self.sound_threshold:+.1f}, {took*1000:.0f} ms) — mark put back")
        return new_text

    def process(self, data: np.ndarray) -> None:
        if not self.busy.acquire(blocking=False):
            log("the previous take is still being processed — skipping")
            return
        try:
            t_all = time.perf_counter()
            # The pre-roll is sound from before the key went down. It is fed to
            # the recognizer on purpose (that is what saves the first syllable)
            # but it is not speech time and must not count as such.
            pre_n = min(len(data), int(self.recorder.last_preroll_s * audio_mod.TARGET_SR))
            secs = (len(data) - pre_n) / audio_mod.TARGET_SR
            peak, rms = audio_mod.loudness(data[pre_n:])
            if secs < MIN_SECONDS:
                self.hud.set("warn", "too short", hide_after=1.2)
                return
            if rms < SILENCE_RMS:
                self.hud.set("warn", "silence on the mic", hide_after=2.0)
                log(f"silence: {secs:.1f} s, peak {peak:.4f}")
                return

            # The level the microphone actually delivered decides the next take.
            level_note = self.gain.adapt(peak, secs)
            if peak >= 0.99:
                self.hud.set("warn", "too loud", hide_after=1.5)
            elif peak < 0.03:
                self.hud.set("warn", "too quiet", hide_after=1.5)

            raw, t_asr = self.transcribe_resilient(audio_mod.normalize(data), secs)
            if not raw.strip():
                self.hud.set("warn", "nothing recognized", hide_after=1.5)
                return

            # The recognizer's stock line for "I heard nothing but had to say
            # something" — subtitle credits it was trained on. Pasting it is
            # worse than pasting nothing: it looks like a real take. The wav
            # stays on disk either way, so nothing is actually lost.
            from .asr import subtitle_ghost

            ghost = subtitle_ghost(raw)
            if ghost:
                self.hud.set("warn", "heard nothing", hide_after=2.0)
                # A long take deserves a loud line: on 25.08.2026 four minutes
                # of speech came back as "Продолжение следует..." and only the
                # log would have shown it.
                how = "silence" if secs < 3 else f"{secs:.0f} s OF SPEECH"
                log(f"the recognizer filled {how} with a stock subtitle line "
                    f"({raw.strip()!r}) — not pasted, audio kept")
                return

            pre, n_pre = self.fixes.apply(raw)
            if self.fix_endings:
                pre, flipped = endings.apply(pre)
            else:
                flipped = []
            was_on = self.polisher.available
            polished, t_pol, note = self.polisher.polish(pre)
            # The corrector coming BACK has been announced since the start
            # (boot(), corrector_back). Its going away was announced nowhere:
            # dictation rightly keeps working on raw text, but silently. On
            # 22.08.2026 twenty-four takes in a row went unpolished between
            # 20:57 and 22:07, on 26.08 another twenty-nine — and the only
            # trace was in the log, read the next day. Said once, on the turn
            # from working to not, so a dead LM Studio is not repeated on
            # every take.
            if was_on and not self.polisher.available:
                log(f"corrector OFF — {self.polisher.reason}"
                    f" (dictation continues; the text comes out raw)")
                self.hud.set("warn", "corrector off", hide_after=3.0)
            final, _ = self.fixes.apply(polished)
            sound_q = self.ask_the_sound(data, raw, final)
            if sound_q is not None:
                final = sound_q

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
                    # Sound captured from BEFORE the key went down. Zero means
                    # the microphone was opened from cold on this press and the
                    # first syllable is at risk — the thing that was happening
                    # on 68% of takes until 29.08.2026.
                    "preroll_s": round(self.recorder.last_preroll_s, 3),
                    "raw": raw,
                    "after_fixes": pre,
                    "final": final,
                    "polish_note": note,
                    # How much the voice at the end sounded like a question
                    # (only measured when nothing else found one).
                    "sound_q": (None if self.last_sound_q is None
                                else round(self.last_sound_q, 2)),
                    "ms_asr": int(t_asr * 1000),
                    "asr_model": self.asr.last_model,
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
                f"(recognized {t_asr:.2f}, corrected {t_pol:.2f}, {note}, "
                f"pasted via {paste_mod.last_route})")
            log(f"  {final}")
            if peak >= 0.99:
                log(f"  the take clipped (peak {peak:.2f}) — the words under the "
                    f"clipping are lost")
            elif peak < 0.03:
                log(f"  the take came out very quiet (peak {peak:.3f})")
            if level_note:
                log(f"  {level_note}")
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


def cmd_bindtoggle(seconds: int = 15) -> None:
    """Binds whatever button you press to "start/stop recording without holding".

    Press it once and the app works out on its own whether the mouse sent a
    button or the mouse software sent a keystroke, then writes the answer into
    config.toml. There is nothing to look up and nothing to type by hand.

    A gaming mouse only sends its extra buttons (G7, G8...) if they are mapped
    to something in its own software. If nothing arrives, that is what the
    message says.
    """
    import time

    import keyboard
    from pynput import mouse

    caught: list[tuple[str, str]] = []  # (what to write in config, human name)
    MOUSE_NAMES = {
        mouse.Button.x1: ("x1", 'the side "back" button'),
        mouse.Button.x2: ("x2", 'the side "forward" button'),
        mouse.Button.middle: ("middle", "the wheel"),
    }
    # A modifier on its own is not a button: it is what a real key is pressed
    # WITH. Binding one would fire on every Ctrl press in the system.
    MODIFIERS = {"ctrl", "alt", "shift", "left ctrl", "right ctrl", "left alt",
                 "right alt", "left shift", "right shift", "windows"}
    # Ordinary typing is never a hotkey for this. Without the filter, one letter
    # typed while the listener is up would be written into the settings and the
    # dictation would start recording every time that letter was pressed.
    TYPING = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
        "space", "enter", "backspace", "tab", "esc", "delete", "up", "down",
        "left", "right", ",", ".", "/", ";", "'", "[", "]", "\\", "-", "=", "`",
    }

    def on_key(e):
        if e.event_type != "down" or caught:
            return
        name = (e.name or "").lower()
        if not name or name in MODIFIERS or name in TYPING or len(name) == 1:
            return
        if name in taken:
            return
        caught.append(("key", name))

    def on_click(x, y, button, pressed):  # noqa: ARG001
        if not pressed or caught or button not in MOUSE_NAMES:
            return
        if MOUSE_NAMES[button][0] in taken_buttons:
            return
        caught.append(("button", MOUSE_NAMES[button][0]))

    # Keys the app already answers to. Catching one of them means the owner was
    # simply dictating while this was listening, not choosing a button — and
    # binding it would make one press do two jobs at once.
    cfg = cfg_mod.load()
    taken = set()
    hk = cfg.get("hotkey", {})
    for name in (hk.get("name"), hk.get("fix_hotkey"), hk.get("flip_hotkey"),
                 cfg.get("repaste", {}).get("key")):
        if name:
            taken |= {p.strip().lower() for p in str(name).split("+")}
    taken_buttons = {
        b.strip().lower()
        for b in str(cfg.get("repaste", {}).get("button", "")).split(",")
        if b.strip()
    }

    print("Press the button you want — G8, or any other.")
    print("One press starts recording, the next one stops it.")
    print(f"Listening {seconds} s. Do not touch left or right mouse.\n")

    listener = mouse.Listener(on_click=on_click)
    listener.start()
    keyboard.hook(on_key)
    for _ in range(seconds * 10):
        if caught:
            break
        time.sleep(0.1)
    keyboard.unhook_all()
    listener.stop()

    if not caught:
        print("Nothing arrived.")
        print("A gaming mouse sends its extra buttons only when they are")
        print("mapped in its own software. Open Logitech G HUB, put any free")
        print("key on G8 — F16 will do — and run this again.")
        return

    field, value = caught[0]
    other = "button" if field == "key" else "key"
    text = cfg_mod.CONFIG_PATH.read_text(encoding="utf-8")
    block = (
        "\n[handsfree]\n"
        "# One press starts recording, the next stops it — nothing to hold.\n"
        "# Written by `run.ps1 bindtoggle`.\n"
        "enabled = true\n"
        f'{field} = "{value}"\n'
        f'{other} = ""\n'
        "# Swallow it so Windows does not also do its usual job with it. On by\n"
        "# default here: a mouse button sends a key that already means something\n"
        "# (Print Screen opens the Snipping Tool, the side buttons go back and\n"
        "# forward in a browser), and that would fire on every recording.\n"
        "suppress = true\n"
    )
    if "[handsfree]" in text:
        head, _sep, tail = text.partition("[handsfree]")
        rest = tail.split("\n[", 1)
        text = head + block.lstrip("\n") + ("\n[" + rest[1] if len(rest) > 1 else "")
    else:
        text = text.rstrip("\n") + "\n" + block
    cfg_mod.CONFIG_PATH.write_text(text, encoding="utf-8")

    print(f"Caught: {field} = {value!r}. Written into config.toml.")
    print("Restart the dictation and the button works:")
    print("    .\\start-background.ps1 -Restart")


def cmd_selftest() -> None:
    ok = True
    cfg = cfg_mod.load()
    print("=== self-test ===\n")

    hint = cfg["mic"].get("name", "")
    devices = audio_mod.find_devices(hint, str(cfg["mic"].get("path", "raw")))
    if devices == [None] and hint:
        print(f"[X] microphone {hint!r} not found. List them: run.ps1 mics")
        ok = False
    else:
        idx = devices[0]
        name = "default" if idx is None else audio_mod.sd.query_devices(idx)["name"]
        print(f"[v] microphone: {name} (fallback inputs: {len(devices)-1})")

    print("[.] trying to record 1 second...")
    try:
        rec = audio_mod.Recorder(devices, int(cfg["mic"]["samplerate"]),
                                 preroll_s=0.0, hot_s=0.0)
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


SPOKENLY = Path.home() / "AppData" / "Roaming" / "Spokenly" / "History"
RU_WORD_RE = __import__("re").compile(r"[а-яё]{3,}", __import__("re").IGNORECASE)


def cmd_show(needle: str) -> None:
    """Everything known about one take, found by the label from the page.

    The other half of the "метка" button: he copies a label off the page and
    pastes it into a conversation, and this prints exactly what that take went
    through — what was heard, what the dictionary changed, what the corrector
    did, and where the audio is. Before this, talking about a specific take
    meant describing it from memory and hunting through the logs by hand.

    Accepts the full id, a fragment of it, or a piece of the text.
    """
    import json

    needle = (needle or "").strip().lstrip("#").strip()
    if not needle:
        print("надо сказать, какую надиктовку показать:")
        print("  run.ps1 show 2026-08-29_085812-156")
        print("  run.ps1 show будильник")
        return

    hits = []
    for lf in sorted(cfg_mod.LOG_DIR.glob("*.jsonl")):
        for n, line in enumerate(lf.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rec.setdefault("id", f"{lf.stem}_line{n:04d}")
            hay = f"{rec['id']} {rec.get('raw','')} {rec.get('final','')}".lower()
            if needle.lower() in hay:
                hits.append(rec)

    if not hits:
        print(f"не нашёл ничего по {needle!r}")
        return
    if len(hits) > 6:
        print(f"под {needle!r} подходит {len(hits)} надиктовок, показываю последние 6:")
        hits = hits[-6:]

    marks = {}
    try:
        marks = json.loads(cfg_mod.ROOT.joinpath("state", "marks.json")
                           .read_text(encoding="utf-8"))
    except Exception:
        pass

    for rec in hits:
        mark = marks.get(rec["id"], {})
        pre = float(rec.get("preroll_s", -1))
        print()
        print(f"  {rec['id']}   {rec.get('time','')}   {rec.get('seconds_audio',0)} с")
        print(f"  услышано : {rec.get('raw','')}")
        if rec.get("after_fixes") and rec["after_fixes"] != rec.get("raw"):
            print(f"  словарь  : {rec['after_fixes']}   ({rec.get('fixes_applied',0)} замен)")
        if rec.get("final") and rec["final"] != rec.get("after_fixes"):
            print(f"  правщик  : {rec['final']}")
        print(f"  вставлено: {rec.get('final') or rec.get('raw','')}")
        if mark.get("corrected"):
            print(f"  правил ты: {mark['corrected']}")
        if mark.get("bad"):
            print("  помечено : плохо")
        print(f"  правщик сказал: {rec.get('polish_note','')}")
        # Пустая предзапись = микрофон открывали с нуля, начало фразы под
        # угрозой. Пишется в журнал с 29.08.2026, у записей до этого её нет.
        if pre >= 0:
            print(f"  предзапись: {pre:.2f} с"
                  + ("   <-- НОЛЬ, начало фразы могло срезаться" if pre < 0.05 else ""))
        print(f"  сколько думала: {rec.get('ms_asr',0)} мс распознавание, "
              f"{rec.get('ms_polish',0)} мс правщик")
        print(f"  звук     : {rec.get('wav','')}")


def cmd_learnwords(min_count: int = 3) -> None:
    """Builds the list of your own-language words that you actually say.

    It stops the corrector from turning your words into English terms
    ("сессию" -> "session"). Built from everything already dictated: the
    Spokenly history and this app's own logs. The longer you use it, the fuller
    the list.

    min_count was 1 until 29.08.2026, so a single mishearing became a protected
    word for good — and a protected word is one the corrector is forbidden to
    fix. That is how "мусайба" and "мусыева" got into the list: both are the
    name "Даниил" misheard once each, and their presence there was the reason
    the corrector could not put the name right afterwards. The program had
    locked itself out of repairing its own mistake. Three sightings is the
    threshold for calling something a word of yours rather than a slip.
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


def only_one_copy() -> bool:
    """True if we are the only dictation running.

    A second copy is not merely useless, it breaks the first one: the raw
    microphone path is exclusive, both copies grab the same key, and both load
    the recognizer into the same video card. On 2026-08-23 two copies started
    within the same second and dictation hung on "loading" — from the outside
    it looked like the program was broken.

    A Windows named object: it disappears by itself when the process ends, so
    a crash or a kill never leaves a stale lock behind (a lock file would).
    """
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, r"Local\stt-dictation-single")
        err = ctypes.get_last_error()
        if not handle:
            return True                       # cannot tell — do not stand in the way
        if err == 183:                        # ERROR_ALREADY_EXISTS
            return False
        globals()["_single_lock"] = handle    # hold it for the life of the process
        return True
    except Exception:
        return True


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
    elif cmd == "bindtoggle":
        cmd_bindtoggle(int(args[1]) if len(args) > 1 else 15)
    elif cmd == "selftest":
        cmd_selftest()
    elif cmd == "bench":
        cmd_bench(args[1])
    elif cmd == "dry":
        cmd_dry(args[1:])
    elif cmd == "learnwords":
        cmd_learnwords(int(args[1]) if len(args) > 1 else 3)
    elif cmd == "import-spokenly":
        cmd_import_spokenly()
    elif cmd == "show":
        cmd_show(args[1] if len(args) > 1 else "")
    elif cmd == "dashboard":
        import webbrowser

        cfg = cfg_mod.load()
        url = f"http://127.0.0.1:{cfg.get('web', {}).get('port', 8756)}/"
        print(f"opening {url}")
        print("(the page is served by the app itself — it has to be running)")
        webbrowser.open(url)
    else:
        if not only_one_copy():
            log("dictation is already running — this second copy is closing.")
            log(r"To restart it: .\start-background.ps1 -Restart")
            return
        Dictation().run()


if __name__ == "__main__":
    main()
