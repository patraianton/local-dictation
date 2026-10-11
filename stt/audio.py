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
import collections
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
        # Why and when the microphone was last let go, so that a take that
        # starts with no pre-roll can say what took it (see Dictation.start).
        self.closed_why = "not opened yet"
        self.closed_at = time.perf_counter()
        self.xruns = 0                      # blocks Windows admits it dropped
        # When the driver reported a dropped block, and what it called it. The
        # running total above cannot say which TAKE was hit, so the take's own
        # share is counted from these (see stop). The window starts at the
        # pre-roll, not at the key press: a hole in the half second before the
        # press is a chewed first syllable all the same.
        self._xrun_events = collections.deque(maxlen=256)
        self._take_t0 = 0.0
        self.last_xruns = 0           # dropped blocks inside the take just handed over
        self.last_xrun_kinds: list[str] = []
        # A stop that has been asked for and has not yet handed the audio over
        # (see begin_stop / resume). Set = the stop was taken back.
        self._stop_req = None
        # Where the take proper begins in the buffer, i.e. how many samples of
        # pre-roll came before the key press (see new_audio).
        self._take_from = 0

    @property
    def device(self):
        return self.recipe[0] if self.recipe else self.devices[0]

    @property
    def api(self) -> str:
        return api_of(self.device)

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        self._last_block_at = time.perf_counter()
        block = indata if indata.ndim == 1 else indata.mean(axis=1)
        block = np.asarray(block, dtype=np.float32).copy()
        with self._lock:
            if status:
                self.xruns += 1
                self._xrun_events.append((self._last_block_at, str(status)))
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

    @property
    def pending_preroll_s(self) -> float:
        """Seconds of sound from before the press in the take being recorded."""
        return self._pending_preroll

    def _close(self, why: str = "closed") -> None:
        with _pa_lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                self.closed_why, self.closed_at = why, time.perf_counter()
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
                        self._close("it stopped delivering sound")
                    elif (self._stream is not None and self._standin()
                          and hot and may_hold
                          and now - self._relooked_at >= self.relook_s):
                        # On a stand-in: let it go so that _open() below can
                        # look for the wanted microphone.
                        self._close("a stand-in, let go to look for the wanted one")
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
                                self.closed_why = "busy in another program"
                        continue
                    if not hot or not may_hold:
                        self._close("not held any longer (hot_ms ran out)" if not hot
                                    else "handed to a program in yield_to")

        self._keeper = threading.Thread(target=loop, daemon=True)
        self._keeper.start()

    def release(self) -> None:
        """Gives the microphone back to other programs right now."""
        with _pa_lock:
            if not self._armed:
                self._close("given back on request")

    def start(self) -> bool:
        """Begins a take, keeping whatever pre-roll is already in the buffer.

        True when a take was begun. False when one was already being recorded
        and nothing changed — until 01.10.2026 that case was silent, and the
        caller went on to show "recording" over a take that was about to end
        (a key press during the tail of the previous take, see resume()).
        """
        with _pa_lock:
            if self._armed:
                return False
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
                self._take_t0 = time.perf_counter() - self._pending_preroll
                self._take_from = self._held
            self._armed = True
            return True

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

    def new_audio(self, mark: int | None = None) -> tuple[np.ndarray, int]:
        """The sound of the take in progress since `mark`, as 16 kHz mono.

        Returns (audio, new_mark). `mark` is what the previous call returned;
        None = from the start of the take, i.e. without the pre-roll — sound
        from before the key press is nobody's speech in this take (see
        VoiceWatch). Instant and read-only: the snapshot of the chunk list and
        the count are taken under one lock, so a block that arrives meanwhile
        is counted exactly once, by the next call. Empty when no take is being
        recorded or the buffer has been handed over since `mark`.
        """
        with self._lock:
            held, armed, sr = self._held, self._armed, self._open_sr
            chunks = list(self._chunks)             # references; blocks are never edited
            start = self._take_from if mark is None else mark
        if not armed or held <= start or start < 0:
            # Not recording, nothing new, or the buffer was replaced since the
            # mark (a mark beyond what is held): start over from what is there.
            return np.zeros(0, dtype=np.float32), (held if held < start else start)
        want, got, parts = held - start, 0, []
        for c in reversed(chunks):
            parts.append(c)
            got += len(c)
            if got >= want:
                break
        audio = np.concatenate(parts[::-1])[-want:]
        return _resample(audio, sr or TARGET_SR, TARGET_SR), held

    @staticmethod
    def _rms(a: np.ndarray) -> float:
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0

    def _wait_tail(self, max_tail_s: float, quiet_ms: int, rel: float,
                   request=None) -> None:
        """Keeps recording for a moment after the key is released.

        People release the key while still finishing the last word, and that
        word was being thrown away. Measured 2026-08-15: 47% of takes had not a
        single quiet frame at the end, i.e. the audio was cut mid-word.

        It does not always wait the full time: as soon as silence starts, it
        stops. Release during a pause and the delay is almost nothing.

        "Silence" is counted in SAMPLES THAT HAVE ARRIVED, not in polls. The
        driver hands over blocks of 4096 samples (85 ms at 48 kHz) and this
        loop looks every 20 ms, so three polls out of four see nothing new.
        Until 01.10.2026 those empty polls were counted as quiet: the wanted
        120 ms of silence was reached 120 ms after the last block no matter
        what was in it, i.e. one quiet block was enough. Now an empty poll tells
        nothing, and the tail ends after quiet_ms of audio really received.
        Replayed over 479 recordings of 28.09-01.10 (blocks of 85 ms, release at
        a random point of the block clock) the stop comes later than the old
        rule by about 10 ms when the key goes up in a pause and by 40-60 ms
        (median) when it goes up as the word ends — two quiet blocks instead
        of one.

        The longest tail was 400 ms until 01.10.2026 and is 700 now. Measured
        on 30.09 over 1855 takes of 17-30.09: 24 of them (1.3%) were cut at the
        limit while the voice was still audible. Only those take longer; a take
        that ends cleanly stops on silence as before.

        `request` (see begin_stop): if it is set while waiting, the stop has
        been taken back by resume() and there is nothing left to wait for.
        """
        if max_tail_s <= 0:
            return
        # "Speech level" is taken from the last second of the recording.
        base = self._rms(self._last_samples(self._open_sr))
        thr = max(base * rel, 0.004)
        step = 0.02
        need = int(max(0, quiet_ms) / 1000.0 * self._open_sr)
        quiet = 0                   # samples of quiet audio in a row
        now_t = time.perf_counter()
        deadline = now_t + max_tail_s
        last_arrival = now_t
        mark = self._samples()
        while time.perf_counter() < deadline:
            if request is not None:
                if request.wait(step):
                    return
            else:
                time.sleep(step)
            now = self._samples()
            new = now - mark
            if new <= 0:
                # Nothing arrived, which says nothing about the sound. But a
                # stream that has stopped delivering altogether (the microphone
                # pulled out) must not hold the take for the whole tail.
                if time.perf_counter() - last_arrival > 0.5:
                    return
                continue
            mark = now
            last_arrival = time.perf_counter()
            if self._rms(self._last_samples(new)) <= thr:
                quiet += new
            else:
                quiet = 0
            if quiet >= need:
                return

    def begin_stop(self):
        """Registers that this take is about to be stopped. Instant, never waits.

        Returns the request, or None when no take is being recorded. The caller
        hands it to stop() — which may be running on another thread a moment
        later — so that a key press arriving in between can already take the
        stop back with resume(). Without it the press would fall into the gap
        between "the key was released" and "the thread that stops has started".
        """
        with self._lock:
            if self._stream is None or not self._armed:
                return None
            req = threading.Event()
            self._stop_req = req
            return req

    def resume(self) -> bool:
        """Takes back a stop that has been asked for and has not yet finished.

        True: the take goes on exactly as if the key had never been released —
        same buffer, same pre-roll, the tail that was already recorded stays in
        — and the pending stop() will return None. False: there is nothing to
        take back (no stop is pending, or it has already handed the audio over;
        then the caller starts a new take instead).

        Why. After the release, stop() keeps the take armed for up to the whole
        tail while it records it. A key press in that window used to reach
        start(), which saw "armed" and quietly returned; the caller showed
        "recording", and at the next release stop() gave back nothing: the
        whole phrase vanished without a trace. Measured over 10.08-30.09.2026,
        27 takes began within 0.18 s of the end of the previous one, 17 of them
        in September after takes that were cut while still audible: those are
        the presses that land on this window.

        The decision is one step under the lock that stop() also takes to hand
        the audio over, so exactly one of the two wins.
        """
        with self._lock:
            req = self._stop_req
            if req is None or req.is_set() or not self._armed:
                return False
            req.set()
            self._stop_req = None
            return True

    def stop(self, tail_s: float = 0.0, quiet_ms: int = 120,
             quiet_rel: float = 0.12, request=None):
        """Stops the take and returns 16 kHz mono audio.

        tail_s — how long to keep recording after the stop command, at most.
        request — from begin_stop(), when the caller registered the stop ahead
        of calling this.

        Returns None, and leaves the take recording, when resume() took the
        stop back; the caller must not process anything then.
        """
        req = request if request is not None else self.begin_stop()
        if req is not None and req.is_set():
            return None                 # taken back before we even started
        if req is None or self._stream is None or not self._armed:
            self._armed = False
            self.last_preroll_s = 0.0
            self.last_xruns = 0
            self.last_xrun_kinds = []
            return np.zeros(0, dtype=np.float32)
        try:
            self._wait_tail(tail_s, quiet_ms, quiet_rel, req)
        except Exception:
            pass
        with self._lock:
            # The one decision. resume() sets the request under this same lock,
            # so either it is already set here (the take stays armed, every
            # chunk where it was), or it can no longer be set (the audio is
            # handed over below, and resume() answers False).
            if req.is_set():
                return None
            if self._stop_req is req:
                self._stop_req = None
            chunks = self._chunks
            self._chunks, self._held = [], 0
            self._armed = False
            hits = [kind for t, kind in self._xrun_events if t >= self._take_t0]
        self.last_xruns = len(hits)
        self.last_xrun_kinds = sorted(set(hits))
        self.last_preroll_s = self._pending_preroll
        if self.hot_s > 0:
            # Hold the microphone a little longer: the next phrase usually
            # follows within seconds, and then its beginning is not lost.
            self._hot_until = time.perf_counter() + self.hot_s
            self._ensure_keeper()
        else:
            self._close("not held between takes (hot_ms = 0)")
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


# A take whose loudest sample stays under this is pulled up by normalize().
BOOST_BELOW = 0.25


def normalize(audio: np.ndarray) -> np.ndarray:
    """Quiet takes are pulled up; loud ones are left alone."""
    peak, _ = loudness(audio)
    if 0.0 < peak < BOOST_BELOW:
        return (audio * (0.5 / peak)).astype(np.float32)
    return audio


# Voice in a hands-free take (see VoiceWatch): a 0.3 s window is voiced at an rms
# of 0.004 or more (-48 dBFS), and voice is 3 voiced windows out of the last 4.
# Measured 01.10.2026 on the 6,425 recordings of 24.08-01.10 (15.4 hours, the
# raw microphone at a held gain), against the recognizer's own speech detector
# (Silero VAD on the CPU, no padding) as the truth:
#   * the median window of speech reads 0.052, the median window of an idle take
#     0.001 (0.0006-0.0024 take by take);
#   * 98.4% of the windows that are speech reach 0.004; 11.8% of the windows
#     that are not speech do too (breaths and tails beside words, room noise);
#   * the first speech of a take was found 0.74 s after it began (median; p99
#     1.2 s, worst 1.7 s) in all but 4 of 6,079 takes, all four shorter than
#     2 s; 99.9-100% of the speech stretches over 1 s are found, 94.8% of those
#     of 0.6-1 s (a lone "да");
#   * on idle audio (the 17 near-empty long takes, farther than 1 s from any
#     speech) 6.2% of the windows reach 0.004, and voice is declared about 61
#     times an hour (counting stretches more than 3 s apart). That only delays
#     the 60 s rule; it can never cut anybody off.
# The level is the one number that matters, and it was chosen so that a wrong
# answer costs time and never speech: 0.012 (the median speech window over four)
# would have missed 8 of the 13 quietest dictations (take medians 0.004-0.011,
# among them two long ones of 233 and 218 words) and cancelled them.
# Three of four, and not one, is what keeps a click, a thump or a cough (one or
# two windows) from counting: the burst that opened the take of 01.10.2026 is
# a single loud window. One measure that did not help: the median of the three
# 100 ms parts of a window instead of its rms (less speech found, no fewer
# false stretches on idle audio).
VOICE_WINDOW_S = 0.3
VOICE_RMS = 0.004
VOICE_NEED = 3
VOICE_SPAN = 4

# How long a hands-free take may have nobody in it before it is cancelled, and
# how long it may stay quiet after speech before it is ended. 90 and 60 since
# 01.10.2026 (the first draft had 45 for the cancel). Replayed on the 103 takes
# of 45+ s in the journals (10.08-01.10, 86 of them real dictations):
#   cancel 45 s: one real dictation lost, the 158-word take of 17.09 that was
#                begun by a tap and spoken 130 s later (70 s of speech);
#   cancel 90 s: none lost, and 162 s fewer given back out of the 2,335 s that
#                the 17 near-empty takes ran (972 s against 1,134 s).
# The 90 is no guarantee, only the best of the table: that same take is saved
# at 90 s by luck (a noise at 69.9 s counted as voice), and nothing records
# after a cancel, so speech that begins later goes to an idle microphone. That
# is why a cancel stays on the screen for a minute (CANCEL_NOTICE_S in
# __main__.py) and is not a quiet 2 s flash. The stop is left at 60: it costs
# at most a few seconds of speech (09-22: a 61 s pause inside a 50-word take,
# 2.5 s said after the stop; 90 would give back 418 s less) and it is
# announced the same way.
SILENT_CANCEL_S = 90.0
IDLE_STOP_S = 120.0


class VoiceWatch:
    """Notices a hands-free take that has nobody speaking in it. No model.

    Why. A tap on the dictation key (or on the mouse side button mapped to it)
    that nobody meant turns hands-free on, and hands-free ends only at the next
    press or at max_seconds. On 01.10.2026 such a take ran 300 s with nobody
    speaking: other audio sat at 15% and the microphone was held for five
    minutes, and the recognizer answered with a stock subtitle line. The
    journals since 10.08 hold 17 takes of 45+ s with at most a dozen words in
    them (2,335 s of ducking and microphone between them); some caught other
    people's talk from a call for minutes.

    Two rules, both on the take's own audio clock (seconds of sound received,
    not the wall clock, so a stalled stream or a sleeping machine cannot fire
    them):
      * no voice at all within `silent_cancel_s` of the start -> "cancel";
      * voice was heard, then `idle_stop_s` without any -> "stop" (an ordinary
        stop: the take is recognized and pasted, nothing is lost).
    0 switches a rule off.

    Replayed over the 103 takes of 45+ s in the journals at 90 s / 60 s (the
    defaults, see SILENT_CANCEL_S): 9 of the 17 near-empty ones are cut (2
    cancelled, 7 stopped), which gives back 972 s of the 2,335 (42%); of the 86
    real dictations none is cancelled and two are stopped, leaving 0 s and 2.5
    s of speech behind. At 45 s the cancel gave back 1,134 s but cut a
    158-word dictation whose speech began 130 s in. The longest silence inside
    a real dictation was 61 s by the speech detector (the next one 53 s): 60 s
    is at the edge of what people do, not far inside it.

    What a cancel does NOT do: a cancelled take keeps its sound (see
    write_dropped), and that sound only shows what the silent stretch held, for
    example speech too quiet for VOICE_RMS. Nothing records after the cancel,
    so speech that begins later is not captured anywhere; the only way to catch
    it is for the person to see that the take is over (the caller keeps a
    notice on the screen and the other audio comes back to full volume). The
    same goes for a stop: what is said after it goes to an idle microphone.

    What counts as voice is cheap on purpose, no model: see VOICE_RMS. It cannot
    tell a voice from anything else that is as loud and as long — someone else's
    talk, music, typing at a steady clip — and then the rules simply do not
    fire and the take ends as it did before, at the next press or at
    max_seconds.

    Feed it the audio of the take with feed(), or let poll() pull it from the
    Recorder. Thread-safe: poll() may be called by more than one watchdog.
    """

    def __init__(self, silent_cancel_s: float = SILENT_CANCEL_S,
                 idle_stop_s: float = IDLE_STOP_S,
                 rms: float = VOICE_RMS, need: int = VOICE_NEED,
                 span: int = VOICE_SPAN, sr: int = TARGET_SR,
                 window_s: float = VOICE_WINDOW_S):
        self.silent_cancel_s = max(0.0, float(silent_cancel_s))
        self.idle_stop_s = max(0.0, float(idle_stop_s))
        self.rms = float(rms)
        self.span = max(1, int(span))
        self.need = max(1, min(int(need), self.span))
        self.sr = int(sr)
        self._win = max(1, int(round(window_s * self.sr)))
        self._rest = np.zeros(0, dtype=np.float32)   # less than one window, not yet counted
        self._seen = 0                               # samples fed so far
        self._recent = collections.deque(maxlen=self.span)   # last windows: voiced or not
        self._windows = 0
        self.heard = False
        self.last_voice_s = 0.0      # end of the last window that made voice
        self._mark = None            # Recorder.new_audio cursor
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.silent_cancel_s > 0 or self.idle_stop_s > 0

    @property
    def elapsed_s(self) -> float:
        """Seconds of the take's sound seen so far (pre-roll not counted)."""
        return self._seen / float(self.sr)

    def feed(self, audio: np.ndarray) -> None:
        """Adds 16 kHz mono sound of the take, in any chunking."""
        if audio is None or not len(audio):
            return
        a = np.asarray(audio, dtype=np.float32)
        self._seen += len(a)
        if len(self._rest):
            a = np.concatenate([self._rest, a])
        n = len(a) // self._win
        if n:
            w = a[: n * self._win].reshape(n, self._win)
            # mean square per window; compared as a square, no sqrt needed
            ms = np.einsum("ij,ij->i", w, w) / self._win
            for v in (ms >= self.rms * self.rms).tolist():
                self._windows += 1
                self._recent.append(v)
                if v and sum(self._recent) >= self.need:
                    self.heard = True
                    self.last_voice_s = self._windows * self._win / float(self.sr)
        self._rest = a[n * self._win:]

    def verdict(self) -> str | None:
        """"cancel", "stop", or None (carry on)."""
        t = self.elapsed_s
        if not self.heard:
            if 0 < self.silent_cancel_s <= t:
                return "cancel"
        elif 0 < self.idle_stop_s <= t - self.last_voice_s:
            return "stop"
        return None

    def poll(self, recorder: "Recorder") -> str | None:
        """Pulls what the recorder has taken since the last call; the verdict."""
        with self._lock:
            audio, self._mark = recorder.new_audio(self._mark)
            self.feed(audio)
            return self.verdict()
