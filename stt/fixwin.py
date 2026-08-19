# -*- coding: utf-8 -*-
"""The "that's wrong" window: fix a word once and it is remembered forever."""
import tkinter as tk
from tkinter import ttk

from .learn import candidate_pairs

BG = "#16181d"
FG = "#e6e9ef"
MUTED = "#8b8f98"


def open_window(root: tk.Tk, record: dict, fixes, on_saved=None) -> None:
    """record: {'raw':..., 'final':...} — the last take."""
    raw = (record or {}).get("raw", "")
    final = (record or {}).get("final", "")
    if not raw and not final:
        return

    win = tk.Toplevel(root)
    win.title("Fix the last take")
    win.configure(bg=BG)
    win.attributes("-topmost", True)
    win.geometry("760x340")

    def lbl(text, color=MUTED, size=9):
        return tk.Label(win, text=text, bg=BG, fg=color, font=("Segoe UI", size),
                        anchor="w", justify="left")

    lbl("What the recognizer heard:").pack(fill="x", padx=16, pady=(14, 2))
    heard = tk.Text(win, height=4, bg="#101216", fg=MUTED, wrap="word", bd=0,
                    font=("Segoe UI", 10), padx=10, pady=8)
    heard.insert("1.0", raw)
    heard.configure(state="disabled")
    heard.pack(fill="x", padx=16)

    lbl("What got pasted — fix it here and press Enter:", FG, 10).pack(
        fill="x", padx=16, pady=(14, 2)
    )
    box = tk.Text(win, height=5, bg="#101216", fg=FG, wrap="word", bd=0,
                  insertbackground=FG, font=("Segoe UI", 11), padx=10, pady=8)
    box.insert("1.0", final)
    box.pack(fill="x", padx=16)

    status = lbl("The corrected text goes to the clipboard — paste it over the old one.")
    status.pack(fill="x", padx=16, pady=(10, 0))

    def save(event=None):  # noqa: ARG001
        corrected = box.get("1.0", "end").strip()
        learned = 0
        if corrected and corrected != final.strip():
            for src, dst in candidate_pairs(final, corrected):
                if fixes.add(src, dst, origin="manual"):
                    learned += 1
            # Also learn from what the recognizer got wrong in the first place.
            for src, dst in candidate_pairs(raw, corrected):
                if fixes.add(src, dst, origin="manual"):
                    learned += 1
            try:
                import pyperclip

                pyperclip.copy(corrected)
            except Exception:
                pass
        if on_saved:
            on_saved(learned)
        win.destroy()
        return "break"

    box.bind("<Return>", save)
    box.bind("<Escape>", lambda e: win.destroy())
    ttk.Button(win, text="Remember (Enter)", command=save).pack(pady=14)

    win.after(80, lambda: (win.focus_force(), box.focus_set(),
                           box.mark_set("insert", "end")))
