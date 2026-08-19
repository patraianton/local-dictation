# -*- coding: utf-8 -*-
"""Кнопка мыши как горячая клавиша — и окно, в которое вставлять.

Нужно для случая «вставка улетела не туда»: наводишь на нужное окно, жмёшь
боковую кнопку — текст оказывается там.
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
    "x1": mouse.Button.x1,     # боковая «назад»
    "x2": mouse.Button.x2,     # боковая «вперёд»
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
    """Верхнее окно под указателем мыши."""
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
    """Делает окно активным.

    Windows не даёт фоновой программе просто так забрать фокус, поэтому
    ненадолго присоединяемся к потоку текущего активного окна — тогда даёт.
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
    """Слушает мышь и зовёт on_press, когда нажата одна из нужных кнопок.

    Кнопок можно указать несколько через запятую — тогда не надо гадать,
    какая из боковых какая: сработает любая.
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
            """Съедаем свои кнопки, чтобы браузер не ушёл назад/вперёд."""
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
    """Показывает, какие кнопки шлёт мышь. Ничего не перехватывает."""
    import time

    seen = {}
    names = {
        mouse.Button.left: "левая", mouse.Button.right: "правая",
        mouse.Button.middle: "средняя (колёсико)",
        mouse.Button.x1: "боковая «назад» — в настройках x1",
        mouse.Button.x2: "боковая «вперёд» — в настройках x2",
    }

    def on_click(x, y, button, pressed):  # noqa: ARG001
        if not pressed or button in seen:
            return
        seen[button] = True
        print(f"  {names.get(button, str(button))}")

    print(f"Жми кнопки мыши, которые хочешь отдать под вставку. Слушаю {seconds} секунд.")
    print("(левую и правую не трогай — они и так заняты)\n")
    listener = mouse.Listener(on_click=on_click)
    listener.start()
    time.sleep(seconds)
    listener.stop()

    print()
    free = [b for b in seen if b not in (mouse.Button.left, mouse.Button.right)]
    if not free:
        print("Свободных кнопок не поймал.")
        print("Если боковых нет — впиши в config.toml -> [repaste] key = \"f14\"")
    else:
        print("Годятся. Впиши выбранное в config.toml -> [repaste] button")