# -*- coding: utf-8 -*-
"""Вставка текста в то окно, где стоит курсор.

Через буфер обмена, а не посимвольным набором: набор кириллицы посимвольно
медленный и ломается в терминалах. Ctrl+V работает везде одинаково.
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
    """Кладёт текст в буфер, жмёт Ctrl+V, потом возвращает буфер как был."""
    if not text:
        return False
    saved = _read_clipboard()
    if not _copy(text):
        return False
    time.sleep(0.04)  # буфер обмена в Windows успевает не сразу
    keyboard.send(hotkey)

    if saved and restore_after > 0:
        def restore():
            time.sleep(restore_after)
            _copy(saved)

        threading.Thread(target=restore, daemon=True).start()
    return True
