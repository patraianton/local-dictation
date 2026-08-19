# -*- coding: utf-8 -*-
"""Reading config.toml and the user dictionaries."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
GLOSSARY_PATH = ROOT / "glossary.txt"
MYWORDS_PATH = ROOT / "mywords.txt"
FIXES_PATH = ROOT / "fixes.tsv"
CANDIDATES_PATH = ROOT / "state" / "candidates.json"
# Where the volume of other apps is written down while you dictate. If the app
# gets killed mid-recording, the next start reads this file and gives the sound
# back.
DUCK_STATE_PATH = ROOT / "state" / "duck.json"
LOG_DIR = ROOT / "logs"
REC_DIR = ROOT / "recordings"


def load() -> dict:
    with open(CONFIG_PATH, "rb") as fh:
        return tomllib.load(fh)


def glossary() -> list[str]:
    """Terms from glossary.txt, in file order."""
    if not GLOSSARY_PATH.exists():
        return []
    out = []
    for line in GLOSSARY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def mywords() -> set[str]:
    """Ordinary words of the speaker's own language.

    The corrector is not allowed to swap these for English terms: "сессию" has
    to stay "сессию" instead of turning into "session". The list is built from
    the speaker's own transcripts with `run.ps1 learnwords`.
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
    """Writes the chosen corrector model into config.toml.

    Rewrites one line instead of the whole file: every setting in config.toml
    carries a comment explaining why it is set that way, and those must survive.
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
