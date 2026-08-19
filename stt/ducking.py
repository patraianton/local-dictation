# -*- coding: utf-8 -*-
"""Приглушение чужого звука на время диктовки.

Зажал клавишу — YouTube, музыка и всё остальное становятся тихими. Отпустил —
возвращаются как были. Своей громкости не касаемся.

Правило то же, что и везде в этой программе: если что-то пошло не так — молча
не делаем ничего. Диктовка из-за звука ломаться не должна.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path


def log(msg: str) -> None:
    # Тот же вид, что и в основном журнале. Отдельно, чтобы не тащить __main__.
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Session:
    """Один источник звука. Обёртка, чтобы Windows-часть можно было подменить."""

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
    """Все программы, которые сейчас могут играть звук."""
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
                name=(proc.name() if proc else "система"),
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

    # ---------- что трогаем ----------
    def _targets(self) -> list[Session]:
        out = []
        for s in self._sessions():
            if s.pid == self.own_pid:
                continue
            if s.name.lower() in self.exclude:
                continue
            out.append(s)
        return out

    # ---------- сама работа ----------
    def _do_duck(self) -> None:
        saved: dict[int, tuple[str, float]] = {}
        for s in self._targets():
            vol = s.volume()
            # Тише, чем нам надо, — не трогаем: иначе мы сделаем ГРОМЧЕ.
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
            # Имя сверяем: номер процесса Windows переиспользует, и не хотелось бы
            # выкрутить громкость чужой программе, занявшей освободившийся номер.
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
                log(f"звук приглушить/вернуть не вышло: {type(exc).__name__}: {exc}")

    # ---------- наружу ----------
    def duck(self) -> None:
        """Приглушить. Возвращается сразу, работа идёт в стороне."""
        if not self.enabled:
            return
        self._want = True
        threading.Thread(target=self._apply, daemon=True).start()

    def restore(self) -> None:
        """Вернуть как было."""
        if not self.enabled:
            return
        self._want = False
        threading.Thread(target=self._apply, daemon=True).start()

    # ---------- страховка от падения ----------
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
        """Вернуть громкость после того, как программу убили во время записи.

        Зовётся один раз при запуске. Без этого чужой звук остался бы тихим
        навсегда, и человек бы не понял, почему.
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
            log(f"вернул громкость {n} программам после прошлого запуска")
        else:
            self._write_state({})
        return n
