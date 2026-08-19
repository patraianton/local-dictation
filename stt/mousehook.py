# -*- coding: utf-8 -*-
"""A mouse button as a hotkey, plus finding the window to paste into.

For the "it pasted into the wrong window" case: point at the right window,
press a side button, and the text lands there.
"""
import ctypes
from ctypes import wintypes

from pynput import mouse

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GA_ROOT = 2
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208

BUTTONS = {
    "x1": mouse.Button.x1,     # side "back"
    "x2": mouse.Button.x2,     # side "forward"
    "middle": mouse.Button.middle,
}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


def window_under_cursor() -> int:
    """The topmost window under the mouse pointer."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.WindowFromPoint(pt)
    return user32.GetAncestor(hwnd, GA_ROOT) if hwnd else 0


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def focus(hwnd: int) -> bool:
    """Brings a window to the foreground.

    Windows will not let a background app just take focus, so we briefly attach
    to the input thread of the currently active window — then it allows it.
    """
    if not hwnd or hwnd == user32.GetForegroundWindow():
        return bool(hwnd)
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None)
    attached = bool(user32.AttachThreadInput(cur, other, True)) if other else False
    try:
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, other, False)
    return user32.GetForegroundWindow() == hwnd


class Hook:
    """Listens to the mouse and calls on_press for the wanted buttons.

    Several buttons may be listed, comma-separated — then there is no need to
    guess which side button is which: any of them fires.
    """

    def __init__(self, buttons: str, on_press, suppress: bool = False):
        names = [b.strip().lower() for b in (buttons or "").split(",") if b.strip()]
        self.buttons = {BUTTONS[n] for n in names if n in BUTTONS}
        self.on_press = on_press
        self.suppress = suppress
        self.listener = None

    @property
    def names(self) -> str:
        back = {v: k for k, v in BUTTONS.items()}
        return ", ".join(sorted(back[b] for b in self.buttons))

    def start(self) -> bool:
        if not self.buttons:
            return False

        want_x = {1 for b in self.buttons if b is mouse.Button.x1}
        want_x |= {2 for b in self.buttons if b is mouse.Button.x2}
        want_middle = mouse.Button.middle in self.buttons

        def event_filter(msg, data):
            """Swallow our buttons so the browser does not go back/forward."""
            if not self.suppress:
                return True
            if want_x and msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                info = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if ((info.mouseData >> 16) & 0xFFFF) in want_x:
                    self.listener.suppress_event()
            elif want_middle and msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                self.listener.suppress_event()
            return True

        def on_click(x, y, button, pressed):  # noqa: ARG001
            if pressed and button in self.buttons:
                try:
                    self.on_press()
                except Exception:
                    pass

        self.listener = mouse.Listener(
            on_click=on_click, win32_event_filter=event_filter
        )
        self.listener.start()
        return True

    def stop(self) -> None:
        if self.listener:
            self.listener.stop()


def watch(seconds: int = 12) -> None:
    """Shows which buttons the mouse sends. Intercepts nothing."""
    import time

    seen = {}
    names = {
        mouse.Button.left: "left", mouse.Button.right: "right",
        mouse.Button.middle: "middle (wheel)",
        mouse.Button.x1: 'side "back" — x1 in the settings',
        mouse.Button.x2: 'side "forward" — x2 in the settings',
    }

    def on_click(x, y, button, pressed):  # noqa: ARG001
        if not pressed or button in seen:
            return
        seen[button] = True
        print(f"  {names.get(button, str(button))}")

    print(f"Press the mouse buttons you want to use for pasting. Listening {seconds} s.")
    print("(leave left and right alone — they are already taken)\n")
    listener = mouse.Listener(on_click=on_click)
    listener.start()
    time.sleep(seconds)
    listener.stop()

    print()
    free = [b for b in seen if b not in (mouse.Button.left, mouse.Button.right)]
    if not free:
        print("No free buttons caught.")
        print('No side buttons? Set config.toml -> [repaste] key = "f14"')
    else:
        print("These will do. Put your pick in config.toml -> [repaste] button")