# -*- coding: utf-8 -*-
"""Pastes text into whatever window currently has the cursor.

Through the clipboard, not by typing character by character: typing Cyrillic
one key at a time is slow and breaks inside terminals. Ctrl+V behaves the same
everywhere — everywhere except herdr, see below.
"""
import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes

import keyboard
import pyperclip

# Куда ушла последняя вставка: "ctrl+v" или "herdr:<пане>". Читает __main__,
# чтобы написать это в лог — иначе непонятно, каким путём текст доехал.
last_route = ""

# Окно herdr (терминал Антона) не принимает Ctrl+V: 20.08.2026 три вставки
# подряд из приложения не долетели ни одной буквы, при этом в обычное окно
# (проверено на отдельном окошке ввода) тот же код вставляет нормально. У herdr
# в настройках вообще нет клавиши «вставить» — ctrl+v там занят только под
# картинки в удалённом режиме. Поэтому в herdr текст отдаём не клавишами, а его
# же командой: herdr pane send-text <пане> <текст> — она пишет прямо в панель.
HERDR_EXE_NAME = "herdr.exe"
_HERDR_PATHS = (
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Herdr\bin\herdr.exe"),
    os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Herdr\bin\herdr.exe"),
)
_NO_WINDOW = 0x08000000  # не мигать чёрным окном консоли на каждую вставку

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


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


def foreground_exe() -> str:
    """Имя программы, чьё окно сейчас активно. Пустая строка, если не вышло."""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION: хватает, чтобы узнать путь,
        # и не требует прав администратора.
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def _herdr_exe() -> str:
    found = shutil.which("herdr")
    if found:
        return found
    for path in _HERDR_PATHS:
        if os.path.exists(path):
            return path
    return ""


def _herdr_run(args: list, timeout: float = 2.0):
    exe = _herdr_exe()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe] + args,
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


# Метки «начало/конец вставки» (bracketed paste). Терминал ставит их вокруг текста,
# вставленного из буфера, и программа понимает: это один кусок, а не набор с клавиатуры.
# 21.09.2026: без них диктовка в окно Claude Code разлеталась на куски — send-text отдаёт
# голый текст, и каждый перевод строки внутри надиктованного срабатывал как Enter, то есть
# каждый абзац уходил отдельным сообщением и обрывал работу («Interrupted»). Проверено:
# с метками весь текст ложится в поле ввода одним блоком и ничего не отправляется.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"


def herdr_focused_pane() -> tuple:
    """Панель в фокусе у herdr и что в ней запущено: (id панели, имя агента).

    Имя агента нужно, чтобы решить, заворачивать ли текст в метки вставки: агенту
    (claude/codex) — обязательно, иначе многострочный текст разлетится по строкам.
    Обычной оболочке метки не шлём: не всякая их понимает, покажет мусором.
    """
    raw = _herdr_run(["pane", "list"])
    if not raw:
        return "", ""
    try:
        panes = json.loads(raw)["result"]["panes"]
    except Exception:
        return "", ""
    for pane in panes:
        if pane.get("focused"):
            return pane.get("pane_id", ""), (pane.get("agent") or "")
    return "", ""


def _herdr_paste(text: str) -> str:
    """Вставка через саму herdr. Возвращает id панели или пустую строку."""
    pane, agent = herdr_focused_pane()
    if not pane:
        return ""
    payload = PASTE_START + text + PASTE_END if agent else text
    if _herdr_run(["pane", "send-text", pane, payload], timeout=3.0) is None:
        return ""
    return pane


def paste_text(text: str, hotkey: str = "ctrl+v", restore_after: float = 1.0) -> bool:
    """Отдаёт текст в активное окно. В herdr — её командой, иначе Ctrl+V."""
    global last_route
    if not text:
        return False

    if foreground_exe().lower() == HERDR_EXE_NAME:
        pane = _herdr_paste(text)
        if pane:
            last_route = f"herdr:{pane}"
            return True
        # herdr не ответила — пробуем как обычно, вдруг долетит

    saved = _read_clipboard()
    if not _copy(text):
        return False
    time.sleep(0.04)  # the Windows clipboard needs a moment to settle
    keyboard.send(hotkey)
    last_route = hotkey

    if saved and restore_after > 0:
        def restore():
            time.sleep(restore_after)
            _copy(saved)

        threading.Thread(target=restore, daemon=True).start()
    return True


def erase_and_type(erase: int, text: str) -> bool:
    """Стереть N символов и напечатать text — там же, где стоит курсор.

    Нужно для ctrl+f13 («это был вопрос»): точку в уже вставленном тексте надо
    заменить на знак вопроса. В herdr клавиши извне не доходят так же, как и
    Ctrl+V, поэтому там и стирание, и печать идут её же командами.
    """
    global last_route
    if foreground_exe().lower() == HERDR_EXE_NAME:
        pane, _agent = herdr_focused_pane()
        if pane:
            ok = True
            if erase > 0:
                ok = _herdr_run(
                    ["pane", "send-keys", pane] + ["backspace"] * erase
                ) is not None
            if ok and text:
                ok = _herdr_run(["pane", "send-text", pane, text]) is not None
            if ok:
                last_route = f"herdr:{pane}"
                return True

    for _ in range(erase):
        keyboard.send("backspace")
        time.sleep(0.02)
    if text:
        keyboard.write(text)
    last_route = "keys"
    return True
