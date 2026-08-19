# -*- coding: utf-8 -*-
"""Pastes text into whatever window currently has the cursor.

Through the clipboard, not by typing character by character: typing Cyrillic
one key at a time is slow and breaks inside terminals. Ctrl+V behaves the same
everywhere.
"""
import threading
import time

import keyboard
import pyperclip


def _copy(text: str, attempts: int = 5) -> bool:
    for i in range(attempts):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.03 * (i + 1))
    return False


def _read_clipboard() -> str:
    try:
        return pyperclip.paste()
    except Exception:
        return ""


def paste_text(text: str, hotkey: str = "ctrl+v", restore_after: float = 1.0) -> bool:
    """Puts the text on the clipboard, sends Ctrl+V, then restores the clipboard."""
    if not text:
        return False
    saved = _read_clipboard()
    if not _copy(text):
        return False
    time.sleep(0.04)  # the Windows clipboard needs a moment to settle
    keyboard.send(hotkey)

    if saved and restore_after > 0:
        def restore():
            time.sleep(restore_after)
            _copy(saved)

        threading.Thread(target=restore, daemon=True).start()
    return True
