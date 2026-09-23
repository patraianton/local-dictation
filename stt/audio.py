# -*- coding: utf-8 -*-
"""Recording from the microphone.

Two things here were quietly ruining takes until 2026-08-23, and both are
fixed below.

**Windows was "improving" the sound before we ever saw it.** The PodMic's
capture endpoint carries an extra processing pack (VocaEffectPack, installed
with RODE Central), and on top of that the MME path hands audio over as
16-bit. Measured side by side on the same room noise:

    MME 16 kHz (what dictation used)  level 0.000032, 62% of samples exactly 0
    WASAPI shared 48 kHz              level 0.000076,  6% exactly 0
    WDM-KS 48 kHz (straight from it)  level 0.000383,  0.4% exactly 0, 24 bit

Twelve decibels of the signal, and every pause, were being eaten before
recognition. Worse, the processing adapts: a 29.5-second take on 2026-08-23
starts at 0.10 and slides to 0.02 over ten seconds, which is why saying the
same sentence again in two seconds came out right. So the default path is now
"raw" — WDM-KS, straight off the device, past all of it.

**The microphone opened only after the key went down.** Between 26% and 42% of
takes began with speech already in the first frame, i.e. the first syllable was
never recorded ("Читайся" for "Отчитайся"). Opening costs 66 ms on MME and
105 ms on the raw path, and a person starts talking as they press. So the
stream is now held open for a short while after a take and a rolling half
second is kept: press the key and the sound from *before* the press is already
in hand.

The raw path is exclusive — while dictation holds the microphone no other
program can record. That is why it is only held for `hot_ms` after a take and
then let go.
"""
import threading
import time

import numpy as np
import sounddevice as sd

TARGET_SR = 16000

# PortAudio reads the list of devices once, when it is initialised, and never
# again. A microphone unplugged and put back into ANOTHER USB port is a new
# device to Windows (it even gets a "2-" in front of its name), and the index
# PortAudio handed out for the old one answers "Invalid device" from then on.
# That is how dictation went deaf on 04.09.2026 at 11:39 and stayed deaf until
# a restart 50 minutes later. The only way to see the new table is to tear
# PortAudio down and bring it back, and nothing may be reading the table or
# opening a stream while that happens — hence the lock.
_pa_lock = threading.RLock()


def refresh_devices() -> None:
    """Re-reads the device table. About 50 ms on this machine (04.09.2026)."""
    with _pa_lock:
        try:
            sd._terminate()
        except Exception:
            # Already down (a previous _initialize failed): just bring it up.
            pass
        sd._initialize()


def list_inputs() -> list[dict]:
    """Every input device, with the name of its host API."""
    with _pa_lock:
        apis = sd.query_hostapis()
        devices = list(sd.query_devices())
    out = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            out.append(
                {
                    "index": idx,
                    "name": dev["name"],
                    "hostapi": apis[dev["hostapi"]]["name"],
                    "default_samplerate": int(dev["default_samplerate"]),
                    "channels": dev["max_input_channels"],
                }
            )
    return out


# Windows shows the same microphone once per host API, and they are not equal.
# "raw" goes straight to the device and past every Windows effect; "shared"
# is the old polite order that lets other programs record at the same time.
PATHS = {
    "raw": ("Windows WDM-KS", "Windows WASAPI", "MME", "Windows DirectSound"),
    "shared": ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"),
}
HOSTAPI_ORDER = PATHS["shared"]   # kept for anything that still imports it


def find_devices(name_hint: str, path: str = "raw") -> list:
    """Every matching input for a name fragment, most reliable first.

    None always comes last — the Windows default microphone.
    """
    if not name_hint:
        return [None]
    hint = name_hint.strip().lower()
    matches = [d for d in list_inputs() if hint in d["name"].lower()]
    if not matches:
        return [None]
    order = {name: i for i, name in enumerate(PATHS.get(path, PATHS["raw"]))}
    matches.sort(key=lambda d: order.get(d["hostapi"], 99))
    return [d["index"] for d in matches] + [None]


def find_device(name_hint: str, path: str = "raw"):
    """The first matching input — for self-checks and the log."""
    return find_devices(name_hint, path)[0]


def api_of(device) -> str:
    """The name of the host API a device index belongs to."""
    if device is None:
        return "default"
    try:
        return sd.query_hostapis()[sd.query_devices(device)["hostapi"]]["name"]
    except Exception:
        return "?"


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return audio
    try:
        import soxr

        return soxr.resample(audio, src_sr, dst_sr).astype(np.float32)
    except ImportError:
        # Fallback: averaging neighbouring samples instead of filtering.
        # Worse quality, but better than nothing.
        ratio = src_sr / dst_sr
        n = int(len(audio) / ratio)
        idx = (np.arange(n) * ratio).astype(np.int64)
        return audio[idx].astype(np.float32)


class Recorder:
    """Records mono audio into memory while recording is on.

    Between takes the stream can stay open (`hot_s`) so that the beginning of
    the next phrase is already in the buffer when the key goes down.
    """

    def __init__(self, devices=None, samplerate: int = TARGET_SR,
                 preroll_s: float = 0.5, hot_s: float = 0.0, finder=None,
                 named: bool = False):
        # Accept a single device or a list, so there is something to walk.
        if devices is None or isinstance(devices, int):
            devices = [devices]
        self.devices = list(devices)
        # How to find the microphone again once it has been re-plugged: a
        # callable that returns a fresh device list (find_devices, in
        # practice). None = keep the indices given above.
        self.finder = finder
        # A particular microphone was asked for by name. Then the Windows
        # default input is a stand-in at best (it is ANOTHER microphone — a
        # headset, a webcam) and never a place to settle.
        self.named = bool(named)
        # True once the first choice has opened. From then on nothing else is
        # ever tried in this session: not the same microphone on a shared,
        # Windows-processed path, not another microphone. A busy or absent
        # first choice is a failure to report, not a reason to slide down.
        self.settled = False
        # While on a stand-in, how often to look for the wanted microphone.
        self.relook_s = 30.0
        self.log = lambda msg: None
        self._last_block_at = 0.0    # when the driver last handed us sound
        self._rediscover_at = 0.0    # the device table is re-read at most every few seconds
        self._relooked_at = time.perf_counter()   # last look for the wanted microphone
        self._absent = False         # the wanted microphone is not plugged in
        self.want_sr = samplerate
        self.preroll_s = max(0.0, float(preroll_s))
        self.hot_s = max(0.0, float(hot_s))
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._held = 0                      # samples currently in _chunks
        self._lock = threading.Lock()
        self._open_sr = samplerate
        self._open_ch = 1
        self._armed = False                 # True while a take is being recorded
        self._hot_until = 0.0
        self._retry_at = 0.0   # do not hammer a microphone somebody else has
        self._keeper = None
        self._closing = threading.Event()
        # Optional veto: while it returns False the microphone is let go at
        # once, whatever the timer says. Dictation uses it to stand aside the
        # moment Anton switches to another window — that window may well be
        # the call he is about to join.
        self.should_hold = None
        self.recipe = None  # what worked; reuse it directly next time
        self.last_error = ""
        self.last_preroll_s = 0.0     # of the take just handed over
        self._pending_preroll = 0.0   # of the take being recorded now
        self.xruns = 0                      # blocks Windows admits it dropped

    @property
    def device(self):
        return self.recipe[0] if self.recipe else self.devices[0]

    @property
    def api(self) -> str:
        return api_of(self.device)

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        self._last_block_at = time.perf_counter()
        if status:
            self.xruns += 1
        block = indata if indata.ndim == 1 else indata.mean(axis=1)
        block = np.asarray(block, dtype=np.float32).copy()
        with self._lock:
            self._chunks.append(block)
            self._held += len(block)
            if not self._armed:
                # Idling: keep only the rolling pre-roll, drop the rest.
                cap = int(self.preroll_s * self._open_sr)
                while self._chunks and self._held - len(self._chunks[0]) >= cap:
                    self._held -= len(self._chunks.pop(0))

    def _standin(self) -> bool:
        """On something other than the microphone that was asked for."""
        return self.named and not self.settled

    def _recipes(self):
        """Ways to open the mic, from the preferred one to anything that works."""
        if self.recipe and not self._standin():
            yield self.recipe
            return
        # Settled: the first choice or nothing (see settled). A stand-in walks
        # the whole list every time, first choice first, so it goes back to
        # the wanted microphone the moment that one can be opened.
        devs = self.devices[:1] if self.settled else list(self.devices)
        for dev in devs:
            try:
                info = sd.query_devices(dev, "input")
                own_sr = int(info["default_samplerate"])
                max_ch = int(info["max_input_channels"]) or 1
            except Exception:
                own_sr, max_ch = 48000, 2
            seen = set()
            # The device's own rate first: the raw path only speaks that, and
            # asking for 16 kHz there just wastes a failed open on every start.
            for sr in (own_sr, self.want_sr, 48000, 44100):
                for ch in (1, max_ch):
                    if (sr, ch) in seen or ch < 1:
                        continue
                    seen.add((sr, ch))
                    yield (dev, sr, ch)

    def _open(self) -> None:
        """Opens the stream. Raises if nothing works."""
        with _pa_lock:
            if self._stream is not None:
                return
            now = time.perf_counter()
            if self._standin() and now - self._relooked_at >= self.relook_s:
                # Not on the microphone that was asked for (it was absent or
                # busy when we started): look again now and then, or one
                # plugged in after start-up would never be noticed.
                self._relooked_at = now
                self.rediscover()
            errors = self._try_open()
            if errors is None:
                return
            # Nothing opened. Most often the microphone is simply busy (a
            # browser call, say), but it may also have been re-plugged — and
            # then every index we know is dead until the table is re-read.
            # Not on every attempt: the keeper knocks every two seconds.
            if now >= self._rediscover_at:
                self._rediscover_at = now + 10.0
                self.rediscover()
                more = self._try_open()
                if more is None:
                    self.log("microphone found again after re-reading the "
                             f"device table: {self._describe()}")
                    return
                errors += more
            self.last_error = " | ".join(errors[:3])
            raise RuntimeError(f"could not open the microphone: {self.last_error}")

    def _describe(self) -> str:
        dev = self.recipe[0] if self.recipe else None
        if dev is None:
            return "the Windows default input"
        try:
            return f"{sd.query_devices(dev)['name']} [{api_of(dev)}]"
        except Exception:
            return f"device {dev}"

    def _try_open(self):
        """One pass over the recipes. None once a stream is up, else the errors."""
        errors = []
        for dev, sr, ch in self._recipes():
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=sr, channels=ch, dtype="float32",
                    device=dev, callback=self._callback, blocksize=0,
                )
                stream.start()
            except Exception as exc:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                errors.append(f"{dev}/{sr}Hz/{ch}ch: {str(exc)[:60]}")
                continue
            self._last_block_at = time.perf_counter()
            self._stream = stream
            self._open_sr, self._open_ch = sr, ch
            self.recipe = (dev, sr, ch)
            if not self.settled:
                first = self.devices[0] if self.devices else None
                self.settled = dev == first and (dev is not None or not self.named)
            return None
        if not errors:
            errors.append("the microphone is not plugged in")
        return errors

    def rediscover(self) -> None:
        """Forgets what worked and looks for the microphone afresh."""
        self.recipe = None
        try:
            refresh_devices()
        except Exception as exc:
            self.log(f"could not re-read the device table: {exc}")
        if self.finder is None:
            return
        try:
            found = list(self.finder())
        except Exception:
            found = []
        if self.named and self.settled:
            # Ours or nothing (see settled).
            found = [d for d in found if d is not None][:1]
        elif not found:
            found = [None]
        self.devices = found
        absent = self.named and all(d is None for d in found)
        if absent and not self._absent:
            self.log("the microphone is not plugged in — waiting for it")
        self._absent = absent

    def _stale(self, max_gap_s: float = 1.0) -> bool:
        """An open stream that has delivered nothing for a second is dead.

        Pull the microphone out from under a running stream and Windows says
        nothing — the callbacks just stop. Until 04.09.2026 such a stream was
        kept for the whole hot window and every take through it came back
        empty.
        """
        return (self._stream is not None
                and time.perf_counter() - self._last_block_at > max_gap_s)

    def _close(self) -> None:
        with _pa_lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        with self._lock:
            self._chunks = []
            self._held = 0

    def warm(self) -> bool:
        """Opens the mic ahead of time so the first take is not cut either.

        Returns False instead of raising: a busy microphone at startup is not
        a reason to refuse to run.
        """
        if self.hot_s <= 0:
            return False
        try:
            self._open()
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self._hot_until = time.perf_counter() + self.hot_s
        self._ensure_keeper()
        return True

    def _ensure_keeper(self) -> None:
        """One thread that lets the microphone go once nobody is dictating.

        It also takes it BACK. Until 31.08.2026 this thread only ever closed
        the stream: once a program from `yield_to` had taken the microphone
        away, dictation waited for the next key press to reopen it — and a
        stream opened on the key press has an empty pre-roll ring, so that
        take began with its first syllable already missing. Switching to Slack
        for one second therefore cost the beginning of the NEXT phrase, minutes
        later. Now, while the hot window has not run out, the microphone is
        picked up again as soon as the reason to let it go is gone.
        """
        if self._keeper is not None or self.hot_s <= 0:
            return

        def loop() -> None:
            while not self._closing.wait(0.25):
                if self._armed:
                    continue
                now = time.perf_counter()
                hot = now < self._hot_until
                veto = self.should_hold
                may_hold = True
                if veto is not None:
                    try:
                        may_hold = bool(veto())
                    except Exception:
                        may_hold = True
                # Decided and done under one lock. start() arms under the same
                # lock, so a take can no longer be armed onto a stream this
                # thread is about to close — a two-millisecond window (the
                # veto above reads the registry), but a real one.
                with _pa_lock:
                    if self._armed:
                        continue
                    if self._stale():
                        self.log("the microphone stopped delivering sound — "
                                 "letting it go and looking for it again")
                        self._close()
                    elif (self._stream is not None and self._standin()
                          and hot and may_hold
                          and now - self._relooked_at >= self.relook_s):
                        # On a stand-in: let it go so that _open() below can
                        # look for the wanted microphone.
                        self._close()
                    if self._stream is None:
                        if hot and may_hold and now >= self._retry_at:
                            try:
                                self._open()
                            except Exception:
                                # Busy elsewhere — a browser call, say, and a
                                # browser is not on the yield list. Back off,
                                # or this thread would hammer the device four
                                # times a second for the length of the call.
                                self._retry_at = time.perf_counter() + 2.0
                        continue
                    if not hot or not may_hold:
                        self._close()

        self._keeper = threading.Thread(target=loop, daemon=True)
        self._keeper.start()

    def release(self) -> None:
        """Gives the microphone back to other programs right now."""
        with _pa_lock:
            if not self._armed:
                self._close()

    def start(self) -> None:
        """Begins a take, keeping whatever pre-roll is already in the buffer."""
        with _pa_lock:
            if self._armed:
                return
            if self._stale():
                # Dead underneath us (see _stale): the pre-roll in it is
                # silence anyway, so opening afresh loses nothing.
                self._close()
            had_stream = self._stream is not None
            self._open()
            if not had_stream:
                with self._lock:
                    self._chunks, self._held = [], 0
            with self._lock:
                self._pending_preroll = self._held / float(self._open_sr or TARGET_SR)
            self._armed = True

    def _samples(self) -> int:
        with self._lock:
            return self._held

    def _last_samples(self, n: int) -> np.ndarray:
        """The last n samples of what has been recorded so far."""
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        with self._lock:
            chunks = list(self._chunks)
        take, total = [], 0
        for c in reversed(chunks):
            take.append(c)
            total += len(c)
            if total >= n:
                break
        if not take:
            return np.zeros(0, dtype=np.float32)
        a = np.concatenate(list(reversed(take)))
        return a[-n:] if n < len(a) else a

    @staticmethod
    def _rms(a: np.ndarray) -> float:
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0

    def _wait_tail(self, max_tail_s: float, quiet_ms: int, rel: float) -> None:
        """Keeps recording for a moment after the key is released.

        People release the key while still finishing the last word, and that
        word was being thrown away. Measured 2026-08-15: 47% of takes had not a
        single quiet frame at the end, i.e. the audio was cut mid-word.

        It does not always wait the full time: as soon as silence starts, it
        stops. Release during a pause and the delay is almost nothing.
        """
        if max_tail_s <= 0:
            return
        # "Speech level" is taken from the last second of the recording.
        base = self._rms(self._last_samples(self._open_sr))
        thr = max(base * rel, 0.004)
        step = 0.02
        need = quiet_ms / 1000.0
        quiet_for = 0.0
        deadline = time.perf_counter() + max_tail_s
        mark = self._samples()
        while time.perf_counter() < deadline:
            time.sleep(step)
            now = self._samples()
            new = now - mark
            mark = now
            if new <= 0 or self._rms(self._last_samples(new)) <= thr:
                quiet_for += step
            else:
                quiet_for = 0.0
            if quiet_for >= need:
                return

    def stop(self, tail_s: float = 0.0, quiet_ms: int = 120,
             quiet_rel: float = 0.12) -> np.ndarray:
        """Stops the take and returns 16 kHz mono audio.

        tail_s — how long to keep recording after the stop command, at most.
        """
        if self._stream is None or not self._armed:
            self._armed = False
            self.last_preroll_s = 0.0
            return np.zeros(0, dtype=np.float32)
        try:
            self._wait_tail(tail_s, quiet_ms, quiet_rel)
        except Exception:
            pass
        with self._lock:
            chunks = self._chunks
            self._chunks, self._held = [], 0
        self.last_preroll_s = self._pending_preroll
        self._armed = False
        if self.hot_s > 0:
            # Hold the microphone a little longer: the next phrase usually
            # follows within seconds, and then its beginning is not lost.
            self._hot_until = time.perf_counter() + self.hot_s
            self._ensure_keeper()
        else:
            self._close()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32)
        return _resample(audio, self._open_sr, TARGET_SR)

    def close(self) -> None:
        self._closing.set()
        self._armed = False
        self._close()

    @property
    def recording(self) -> bool:
        return self._armed

    @property
    def seconds(self) -> float:
        with self._lock:
            n = self._held
        return n / float(self._open_sr or TARGET_SR)


def loudness(audio: np.ndarray) -> tuple[float, float]:
    """(peak, RMS level) — used to tell whether the mic is silent."""
    if audio.size == 0:
        return 0.0, 0.0
    return float(np.abs(audio).max()), float(np.sqrt(np.mean(audio**2)))


def normalize(audio: np.ndarray) -> np.ndarray:
    """Quiet takes are pulled up; loud ones are left alone."""
    peak, _ = loudness(audio)
    if 0.0 < peak < 0.25:
        return (audio * (0.5 / peak)).astype(np.float32)
    return audio
