# -*- coding: utf-8 -*-
"""Teaches Windows where to find the CUDA libraries that live inside the venv.

Without this ctranslate2 dies with "Library cublas64_12.dll is not found".
Import this BEFORE faster_whisper.
"""
import os
import sys
from pathlib import Path


def enable() -> list[str]:
    added = []
    nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nvidia.is_dir():
        return added
    for sub in sorted(nvidia.iterdir()):
        binpath = sub / "bin"
        if binpath.is_dir():
            os.add_dll_directory(str(binpath))
            os.environ["PATH"] = str(binpath) + os.pathsep + os.environ["PATH"]
            added.append(str(binpath))
    return added
