# -*- coding: utf-8 -*-
"""The microphone's own gain — the Windows "level" slider.

On the RODE PodMic USB that slider is not a software volume: it is the preamp
gain inside the microphone itself, 22..63 dB. Whatever sits there decides how
loud every take comes out, and any app built on WebRTC (Chrome, Zoom, Telegram)
moves it on its own and leaves it moved.

Measured on 2026-08-23 against twelve days of takes:

* 21.08, right after Chrome held the mic (10:57-11:55), the gain jumped to the
  top, 63 dB. Speech clipped and the takes crackled — 5-8 clicks a second.
* By 23.08 it had been dragged down to 42.8 dB. Speech peaked at 0.13 of full
  scale, pauses fell below the last bit of a 16-bit file and became digital
  silence, and the recognizer started inventing words.

Neither day had anything wrong with the microphone. The gain was simply being
moved by other programs, and dictation followed it.

So dictation now keeps the gain where it wants it. A keeper thread puts the
value back whenever something else changes it, and after every take the target
is nudged toward a healthy peak. The whole thing is a no-op on machines where
the audio API is not reachable — dictation never fails because of it.
"""
import json
import math
import threading
import time
from pathlib import Path

# The API is only there on Windows, and only with pycaw installed.
try:  # pragma: no cover - depends on the machine
    import comtypes
    from comtypes import CLSCTX_ALL, POINTER, CoCreateInstance, cast
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    HAVE_API = True
except Exception:  # pragma: no cover
    HAVE_API = False

E_CAPTURE = 1
DEVICE_STATE_ACTIVE = 1


def _endpoint(name_hint: str):
    """The IAudioEndpointVolume of the matching *input*, or None.

    Input only on purpose: the PodMic also registers a headphone output whose
    name matches the same fragment, and turning that one down would just make
    Anton deaf while fixing nothing.
    """
    if not HAVE_API:
        return None, ""
    hint = (name_hint or "").strip().lower()
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        enum = CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        coll = enum.EnumAudioEndpoints(E_CAPTURE, DEVICE_STATE_ACTIVE)
        best = None
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            name = str(AudioUtilities.CreateDevice(dev).FriendlyName)
            if hint and hint not in name.lower():
                continue
            best = (dev, name)
            break
        if best is None:
            return None, ""
        dev, name = best
        vol = cast(
            dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
            POINTER(IAudioEndpointVolume),
        )
        return vol, name
    except Exception:
        return None, ""


class MicGain:
    """Holds the microphone gain at a chosen level and tunes that level.

    Everything here is best-effort. If the endpoint disappears (the mic is
    unplugged, another app grabs it exclusively) the calls quietly do nothing
    and dictation carries on.
    """

    def __init__(self, name_hint: str, cfg: dict, state_path: Path,
                 log=lambda msg: None):
        mic = cfg.get("mic", {})
        self.enabled = bool(mic.get("keep_gain", True))
        self.target_peak = float(mic.get("target_peak", 0.5))
        self.min_db = float(mic.get("min_db", 34.0))
        self.max_db = float(mic.get("max_db", 58.0))
        self.max_step_db = float(mic.get("max_step_db", 6.0))
        self.state_path = Path(state_path)
        self.log = log
        self.name = ""
        self.range = None            # (min, max, step) the device admits
        self.want_db = None          # what we are holding it at
        self._vol = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._corrections = 0
        self._hint = name_hint

    # ---------- plumbing ----------
    @property
    def available(self) -> bool:
        return self._vol is not None

    def open(self) -> bool:
        """Finds the microphone and works out the level to hold."""
        if not self.enabled:
            return False
        self._vol, self.name = _endpoint(self._hint)
        if self._vol is None:
            return False
        try:
            lo, hi, _step = self._vol.GetVolumeRange()
            self.range = (float(lo), float(hi))
            # Never leave the device's own limits, and never go to the very top:
            # 63 dB is exactly where 21.08 clipped.
            self.min_db = max(self.min_db, float(lo))
            self.max_db = min(self.max_db, float(hi))
            now = float(self._vol.GetMasterVolumeLevel())
        except Exception:
            self._vol = None
            return False
        self.want_db = self._clamp(self._remembered(now))
        return True

    def _remembered(self, fallback: float) -> float:
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            return float(saved["db"])
        except Exception:
            return fallback

    def _remember(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"db": round(self.want_db, 2)}), encoding="utf-8"
            )
        except Exception:
            pass

    def _clamp(self, db: float) -> float:
        return max(self.min_db, min(self.max_db, float(db)))

    def current_db(self):
        if self._vol is None:
            return None
        try:
            return float(self._vol.GetMasterVolumeLevel())
        except Exception:
            return None

    def _reopen(self) -> bool:
        """Finds the endpoint again after it vanished. want_db is kept: the
        level is ours, not the device's."""
        vol, name = _endpoint(self._hint)
        if vol is None:
            return False
        try:
            float(vol.GetMasterVolumeLevel())
        except Exception:
            return False
        self._vol, self.name = vol, name
        return True

    def _apply(self) -> bool:
        if self._vol is None or self.want_db is None:
            return False
        try:
            self._vol.SetMasterVolumeLevel(float(self.want_db), None)
            return True
        except Exception:
            return False

    # ---------- keeping ----------
    def start(self, period_s: float = 2.0) -> None:
        """Puts the gain back whenever something else moves it.

        Runs off the dictation path entirely: pressing the key never waits on
        the audio API, and a program that fights us for the slider is written
        into the log instead of quietly ruining the next take.
        """
        if not self.available or self._thread is not None:
            return
        self._apply()

        def loop() -> None:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            misses = 0
            while not self._stop.wait(period_s):
                with self._lock:
                    now = self.current_db()
                    if now is None:
                        # The endpoint is gone: the microphone was unplugged
                        # or re-plugged (04.09.2026 — it came back as a new
                        # device and the gain was not held until a restart).
                        # Look for it again every third round, i.e. every
                        # six seconds, and carry on holding the same level.
                        misses += 1
                        if misses % 3 == 0 and self._reopen():
                            self.log(f"mic gain: the microphone is back "
                                     f"({self.name}), holding "
                                     f"{self.want_db:.1f} dB again")
                            self._apply()
                        continue
                    misses = 0
                    if self.want_db is None:
                        continue
                    if abs(now - self.want_db) < 0.4:
                        continue
                    if self._apply():
                        self._corrections += 1
                        # Only worth a line the first few times: a call app can
                        # drag the slider all through a meeting.
                        if self._corrections <= 3:
                            self.log(
                                f"another program moved the mic gain "
                                f"({now:.1f} dB), put it back to {self.want_db:.1f} dB"
                            )

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- tuning ----------
    def adapt(self, peak: float, secs: float) -> str:
        """After a take: moves the target so the next one peaks where we want.

        Returns a short note for the log, or "".
        """
        if not self.available or self.want_db is None:
            return ""
        if peak <= 0.0005 or secs < 0.4:
            return ""            # silence or a slip of the finger tells us nothing
        with self._lock:
            was = self.want_db
            # Clipped takes are the urgent case: 21.08 lost a whole day to them.
            if peak >= 0.98:
                step = -self.max_step_db
            else:
                step = 20.0 * math.log10(self.target_peak / peak)
                step = max(-self.max_step_db, min(self.max_step_db, step))
                # A quiet dead zone, so the gain does not twitch after every word.
                if 0.3 <= peak <= 0.8:
                    return ""
            self.want_db = self._clamp(was + step)
            if abs(self.want_db - was) < 0.4:
                return ""
            self._apply()
            self._remember()
            return f"mic gain {was:.1f} -> {self.want_db:.1f} dB (peak {peak:.2f})"


def describe(gain: MicGain) -> str:
    """One line for the startup log."""
    if not gain.enabled:
        return "mic gain: not touched (keep_gain = false)"
    if not gain.available:
        return "mic gain: cannot be read on this machine — left as it is"
    lo, hi = gain.range or (0.0, 0.0)
    return (
        f"mic gain: holding {gain.want_db:.1f} dB "
        f"(device allows {lo:.0f}..{hi:.0f}, we stay inside "
        f"{gain.min_db:.0f}..{gain.max_db:.0f})"
    )
