# -*- coding: utf-8 -*-
"""A dot in the corner of the screen: recording / thinking / done.

The window deliberately never takes focus — otherwise the paste would land in
the wrong place.
"""
import ctypes
import queue
import time
import tkinter as tk

TRANSPARENT = "#010203"

COLORS = {
    "idle": None,
    "rec": "#e5484d",       # recording (key held)
    "lock": "#f5a524",      # recording hands-free
    "think": "#ffd166",     # recognizing / correcting
    "ok": "#3dd68c",        # pasted
    "warn": "#8b8f98",      # heard nothing
    "err": "#c85cff",       # broke
}

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020


class Hud:
    def __init__(self, cfg: dict):
        h = cfg.get("hud", {})
        self.enabled = bool(h.get("enabled", True))
        self.size = int(h.get("size", 16))
        self.margin = int(h.get("margin", 28))
        self.q: queue.Queue = queue.Queue()
        self.root = None
        self.win = None
        self._state = "idle"
        self._text = ""
        self._hide_at = 0.0
        self._rec_started = 0.0
        self._visible = False

    # --- callable from any thread ---
    def set(self, state: str, text: str = "", hide_after: float = 0.0) -> None:
        self.q.put((state, text, hide_after))

    # --- internals, main thread only ---
    def build(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=TRANSPARENT)
        try:
            win.wm_attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        w, hgt = 220, max(self.size + 10, 26)
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{w}x{hgt}+{sw - w - self.margin}+{sh - hgt - self.margin - 40}")

        self.canvas = tk.Canvas(
            win, width=w, height=hgt, bg=TRANSPARENT, highlightthickness=0, bd=0
        )
        self.canvas.pack()
        pad = (hgt - self.size) // 2
        self.dot = self.canvas.create_oval(
            w - self.size - pad, pad, w - pad, pad + self.size,
            fill=COLORS["rec"], outline="",
        )
        self.label = self.canvas.create_text(
            w - self.size - pad - 10, hgt // 2,
            text="", anchor="e", fill="#c9ced6",
            font=("Segoe UI", 10, "bold"),
        )
        win.withdraw()
        self.win = win
        self._no_focus()
        self.root.after(40, self._pump)

    def _no_focus(self) -> None:
        """The window must never activate or catch clicks."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            if not hwnd:
                hwnd = self.win.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT,
            )
        except Exception:
            pass

    def _pump(self) -> None:
        try:
            while True:
                state, text, hide_after = self.q.get_nowait()
                self._state, self._text = state, text
                self._hide_at = time.time() + hide_after if hide_after else 0.0
                if state in ("rec", "lock") and self._rec_started == 0.0:
                    self._rec_started = time.time()
                if state not in ("rec", "lock"):
                    self._rec_started = 0.0
                self._render()
        except queue.Empty:
            pass

        if self._rec_started:
            self._render()
        if self._hide_at and time.time() > self._hide_at:
            self._state, self._hide_at = "idle", 0.0
            self._render()
        self.root.after(80, self._pump)

    def _render(self) -> None:
        color = COLORS.get(self._state)
        if color is None:
            if self._visible:
                self.win.withdraw()
                self._visible = False
            return
        text = self._text
        if self._rec_started:
            secs = time.time() - self._rec_started
            text = f"{text} {int(secs // 60)}:{int(secs % 60):02d}".strip()
        self.canvas.itemconfig(self.dot, fill=color)
        self.canvas.itemconfig(self.label, text=text)
        # Show only on a state change: calling deiconify constantly flickers
        # and can steal focus from the window we are about to paste into.
        if not self._visible:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self._visible = True

    def run(self, on_ready=None) -> None:
        self.build()
        if on_ready:
            self.root.after(50, on_ready)
        self.root.mainloop()

    def stop(self) -> None:
        if self.root:
            self.root.after(0, self.root.quit)
