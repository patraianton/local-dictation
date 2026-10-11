# -*- coding: utf-8 -*-
"""Two takes pasted less than restore_after apart must give the owner's
clipboard back, not the first take's text (01.10.2026).

    ..\\.venv\\Scripts\\python.exe test_paste_restore.py
"""
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import paste  # noqa: E402

clip = {"v": "OWNER"}


def copy(text):
    clip["v"] = text
    return True


paste._read_clipboard = lambda: clip["v"]
paste._copy = copy
paste.foreground_exe = lambda: "chrome.exe"
paste.keyboard = types.SimpleNamespace(send=lambda key: None)

paste.paste_text("take one", restore_after=0.3)
time.sleep(0.1)
paste.paste_text("take two", restore_after=0.3)
time.sleep(0.6)
if clip["v"] != "OWNER":
    print(f"[X] two quick pastes left {clip['v']!r} on the clipboard")
    sys.exit(1)

clip["v"] = "COPY2"
paste.paste_text("take three", restore_after=0.2)
time.sleep(0.4)
if clip["v"] != "COPY2":
    print(f"[X] a single paste did not restore: {clip['v']!r}")
    sys.exit(1)
print("[ok] clipboard: two quick pastes give the owner's copy back; one paste restores as before")
