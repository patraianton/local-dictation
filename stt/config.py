# -*- coding: utf-8 -*-
"""Чтение config.toml и словарей."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
GLOSSARY_PATH = ROOT / "glossary.txt"
MYWORDS_PATH = ROOT / "mywords.txt"
FIXES_PATH = ROOT / "fixes.tsv"
CANDIDATES_PATH = ROOT / "state" / "candidates.json"
# Куда записываем чужую громкость на время диктовки. Если программу убьют прямо
# во время записи, при следующем запуске мы вернём звук по этому файлу.
DUCK_STATE_PATH = ROOT / "state" / "duck.json"
LOG_DIR = ROOT / "logs"
REC_DIR = ROOT / "recordings"


def load() -> dict:
    with open(CONFIG_PATH, "rb") as fh:
        return tomllib.load(fh)


def glossary() -> list[str]:
    """Термины из glossary.txt в порядке файла."""
    if not GLOSSARY_PATH.exists():
        return []
    out = []
    for line in GLOSSARY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def mywords() -> set[str]:
    """Русские слова, которые Антон реально говорит.

    Корректору запрещено подменять их на английские термины: «сессию» должно
    остаться «сессию», а не превратиться в «session». Список собирается из его
    же расшифровок командой `run.ps1 learnwords`.
    """
    if not MYWORDS_PATH.exists():
        return set()
    out = set()
    for line in MYWORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def ensure_dirs() -> None:
    for d in (LOG_DIR, REC_DIR, CANDIDATES_PATH.parent):
        d.mkdir(parents=True, exist_ok=True)


MODEL_LINE_RE = re.compile(r"^\s*model\s*=")


def set_polish_model(name: str) -> bool:
    """Записывает выбранную модель корректора в config.toml.

    Правим одну строку, а не переписываем файл целиком: в config.toml каждая
    настройка объяснена комментарием, и терять эти объяснения нельзя.
    """
    if not name or not CONFIG_PATH.exists():
        return False
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    in_polish = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_polish = stripped == "[polish]"
            continue
        if in_polish and MODEL_LINE_RE.match(line):
            lines[i] = f'model = "{name}"'
            CONFIG_PATH.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
            return True
    return False
