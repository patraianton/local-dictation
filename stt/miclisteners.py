# -*- coding: utf-8 -*-
"""Who is actually recording from the microphone right now.

Windows keeps this in the registry, under the privacy page's "recent activity"
list: for every program that has ever asked for the microphone there is a
`LastUsedTimeStart` and a `LastUsedTimeStop`. While a program is holding the
microphone and pulling audio out of it, `LastUsedTimeStop` is **0**; the moment
it lets go, the stop time is written.

Measured live on 2026-08-31 with a five-second recording:

    before      python.exe  start=...941252299  stop=...943960526
    during      python.exe  start=...341581328  stop=0          <- holding
    during      python.exe  start=...341581328  stop=0
    after       python.exe  start=...341581328  stop=...401685565

No delay, no polling of the audio engine, no extra dependency. A stream that is
opened but never read does NOT count as recording, which is exactly right: what
matters is whether somebody is really taking sound.

Why dictation needs this. Our own capture goes through WDM-KS, which is
exclusive — measured the same day, while dictation holds the microphone no
other program can open it at all ("Device unavailable", PaErrorCode -9985).
So the microphone has to be handed over to a program that wants to record. It
used to be handed over on the mere fact that such a program's window came to
the front, and Anton keeps Slack and Telegram in front all day: Slack had not
recorded a single second since 13.08.2026, Telegram one minute at 09:51 on
31.08, yet between them they were taking the microphone away all day long, and
with it the pre-roll — a third of the day's takes started with nothing kept
from before the key press, i.e. with the first syllable already gone.

Everything here is best-effort: no registry, no permission, an odd Windows
build — the calls return "nobody is recording" and dictation carries on as
before.
"""
import time

try:  # pragma: no cover - Windows only
    import winreg

    HAVE_REG = True
except Exception:  # pragma: no cover
    HAVE_REG = False

BASE = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)

# Windows stores desktop programs by their path with "\" replaced by "#", and
# Store apps by package family name. Both branches are read: Telegram is a
# Store app on this machine, Zoom and Slack are not.
_cache: tuple[float, dict] = (0.0, {})
CACHE_S = 0.5


def _hives():
    yield winreg.HKEY_CURRENT_USER, BASE + r"\NonPackaged"
    yield winreg.HKEY_CURRENT_USER, BASE


def _read_all() -> dict:
    """{key name: (start, stop)} for every program Windows has a record of."""
    if not HAVE_REG:
        return {}
    out = {}
    for root, path in _hives():
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(key, name)
                start = winreg.QueryValueEx(sub, "LastUsedTimeStart")[0]
                stop = winreg.QueryValueEx(sub, "LastUsedTimeStop")[0]
            except OSError:
                continue
            if start:
                out[name] = (int(start), int(stop))
    return out


def _snapshot() -> dict:
    global _cache
    now = time.monotonic()
    when, data = _cache
    if now - when < CACHE_S:
        return data
    data = _read_all()
    _cache = (now, data)
    return data


def _names_of(key: str) -> set:
    """Every name a registry key could be known by, lower case.

    "C:#Users#user#AppData#Local#slack#app-4.51#slack.exe" -> {"slack.exe", "slack"}
    "TelegramMessengerLLP.TelegramDesktop_t4vj0pshhgkwm"    -> {"telegramdesktop",
                                                               "telegram", ...}

    A Store app is not filed under its .exe at all — Telegram appears as
    "TelegramMessengerLLP.TelegramDesktop_t4vj0pshhgkwm" while its window
    belongs to Telegram.exe — so for those the parts are matched loosely, by
    one containing the other (see _matches).
    """
    key = key.lower()
    names = set()
    if "#" in key or key.endswith(".exe"):
        exe = key.rsplit("#", 1)[-1]
        names.add(exe)
        names.add(exe.removesuffix(".exe"))
        return names
    # A Store app: "Publisher.AppName_hash" — take the app name and its parts.
    body = key.split("_", 1)[0]
    for part in body.split("."):
        if len(part) >= 3:
            names.add(part)
    names.add(body)
    return names


def _matches(exe: str, names: set) -> bool:
    """Does a window's exe name refer to any of these registry names?

    Exact first. Then, only for names long enough not to collide by accident,
    one containing the other: "telegram" against "telegramdesktop".
    """
    exe = exe.lower().removesuffix(".exe")
    if not exe:
        return False
    if exe in names or exe + ".exe" in names:
        return True
    if len(exe) < 4:
        return False
    for name in names:
        n = name.removesuffix(".exe")
        if len(n) < 4:
            continue
        if exe in n or n in exe:
            return True
    return False


def _now_filetime() -> int:
    """Windows FILETIME for now (100 ns since 1601), same clock as the registry."""
    # 11644473600 = seconds between 1601-01-01 and the Unix epoch.
    return int((time.time() + 11644473600) * 10_000_000)


def recording_now() -> set:
    """Names of programs holding the microphone at this moment."""
    live = set()
    for key, (_start, stop) in _snapshot().items():
        if stop == 0:
            live |= _names_of(key)
    return live


def recorded_within(seconds: float) -> set:
    """Names of programs that recorded within the last `seconds` (or still are).

    A call is not one unbroken recording: mute, a screen share, a device swap
    all close the stream for a moment. Without this window the microphone would
    be snatched back in the middle of a Zoom call.
    """
    live = recording_now()
    cutoff = _now_filetime() - int(seconds * 10_000_000)
    for key, (start, stop) in _snapshot().items():
        if stop and stop >= cutoff:
            live |= _names_of(key)
        elif start >= cutoff:
            live |= _names_of(key)
    return live


def is_recording(exe: str, within_s: float = 0.0) -> bool:
    """Is this program (e.g. "zoom.exe") recording now, or within `within_s`?"""
    if not exe:
        return False
    names = recorded_within(within_s) if within_s > 0 else recording_now()
    return _matches(exe, names)
