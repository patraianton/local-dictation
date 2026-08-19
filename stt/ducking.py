# -*- coding: utf-8 -*-
"""Ducking other applications' audio while you dictate.

Hold the key and YouTube, music and everything else go quiet. Release it and
they come back exactly as they were. Our own volume is never touched.

Same rule as everywhere else in this program: if anything goes wrong, do
nothing, quietly. Dictation must not break because of the sound.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path


def log(msg: str) -> None:
    # Same shape as the main log. Kept separate so as not to import __main__.
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Session:
    """One audio source. A wrapper so the Windows part can be faked in tests."""

    def __init__(self, pid: int, name: str, get, set_):
        self.pid = pid
        self.name = name
        self._get = get
        self._set = set_

    def volume(self) -> float | None:
        try:
            return float(self._get())
        except Exception:
            return None

    def set_volume(self, value: float) -> bool:
        try:
            self._set(max(0.0, min(1.0, float(value))))
            return True
        except Exception:
            return False


def windows_sessions() -> list[Session]:
    """Every application that could be playing audio right now."""
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

    out = []
    for s in AudioUtilities.GetAllSessions():
        try:
            ctl = s._ctl.QueryInterface(ISimpleAudioVolume)
        except Exception:
            continue
        proc = s.Process
        out.append(
            Session(
                pid=proc.pid if proc else 0,
                name=(proc.name() if proc else "system"),
                get=ctl.GetMasterVolume,
                set_=ctl.SetMasterVolume,
            )
        )
    return out


class Ducker:
    def __init__(self, cfg: dict, state_path: Path, sessions=None, own_pid=None):
        d = cfg.get("duck", {})
        self.enabled = bool(d.get("enabled", True))
        self.level = float(d.get("level", 0.15))
        self.exclude = {str(n).lower() for n in d.get("exclude", [])}
        self.state_path = Path(state_path)
        self._sessions = sessions or windows_sessions
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self._lock = threading.Lock()
        self._want = False
        self.saved: dict[int, tuple[str, float]] = {}

    # ---------- what we touch ----------
    def _targets(self) -> list[Session]:
        out = []
        for s in self._sessions():
            if s.pid == self.own_pid:
                continue
            if s.name.lower() in self.exclude:
                continue
            out.append(s)
        return out

    # ---------- the work itself ----------
    def _do_duck(self) -> None:
        saved: dict[int, tuple[str, float]] = {}
        for s in self._targets():
            vol = s.volume()
            # Already quieter than our target: leave it, or we would make it LOUDER.
            if vol is None or vol <= self.level:
                continue
            if s.set_volume(self.level):
                saved[s.pid] = (s.name, vol)
        self.saved = saved
        self._write_state(saved)

    def _do_restore(self) -> None:
        if not self.saved:
            self._write_state({})
            return
        by_pid = {s.pid: s for s in self._sessions()}
        for pid, (name, vol) in self.saved.items():
            s = by_pid.get(pid)
            # Check the name too: Windows reuses process ids, and we do not
            # want to set the volume of whatever app took a freed-up id.
            if s is not None and s.name == name:
                s.set_volume(vol)
        self.saved = {}
        self._write_state({})

    def _apply(self) -> None:
        with self._lock:
            try:
                if self._want and not self.saved:
                    self._do_duck()
                elif not self._want and self.saved:
                    self._do_restore()
            except Exception as exc:
                log(f"could not duck/restore audio: {type(exc).__name__}: {exc}")

    # ---------- public ----------
    def duck(self) -> None:
        """Duck. Returns immediately; the work happens on another thread."""
        if not self.enabled:
            return
        self._want = True
        threading.Thread(target=self._apply, daemon=True).start()

    def restore(self) -> None:
        """Put everything back the way it was."""
        if not self.enabled:
            return
        self._want = False
        threading.Thread(target=self._apply, daemon=True).start()

    # ---------- safety net for a crash ----------
    def _write_state(self, saved: dict) -> None:
        try:
            if saved:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(
                    json.dumps({str(k): v for k, v in saved.items()},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
            elif self.state_path.exists():
                self.state_path.unlink()
        except Exception:
            pass

    def recover(self) -> int:
        """Restore volumes after the app was killed mid-recording.

        Called once at startup. Without it, other apps would stay quiet forever
        and nobody would understand why.
        """
        try:
            if not self.state_path.exists():
                return 0
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        self.saved = {int(k): (v[0], float(v[1])) for k, v in raw.items()}
        n = len(self.saved)
        if n:
            self._do_restore()
            log(f"restored the volume of {n} app(s) from the previous run")
        else:
            self._write_state({})
        return n
