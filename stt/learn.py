# -*- coding: utf-8 -*-
"""Обучение на твоей речи.

Три вещи:
1. Журнал — что услышала, что вставила, сколько заняло (logs/ГГГГ-ММ-ДД.jsonl).
2. Звук + текст на будущее дообучение распознавалки (recordings/).
3. Автословарь — если причёсывающая модель раз за разом чинит одно и то же
   слово, оно попадает в fixes.tsv навсегда и дальше чинится мгновенно.
"""
import difflib
import json
import re
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from .fixes import yo_key

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Слова-паразиты: их удаление — не «ошибка распознавания», в словарь не тащим.
FILLERS = {
    "э", "ээ", "эээ", "а", "аа", "ну", "вот", "типа", "как", "бы", "это",
    "самое", "значит", "короче", "так", "там", "то", "есть", "мм", "ммм", "угу",
}


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def has_letters(s: str) -> bool:
    return bool(LETTER_RE.search(s))


def candidate_pairs(raw: str, polished: str, max_span: int = 3) -> list[tuple[str, str]]:
    """Что именно причёсывающая модель переписала. Только замены слов."""
    a, b = tokens(raw), tokens(polished)
    if not a or not b:
        return []
    sm = difflib.SequenceMatcher(
        a=[t.lower() for t in a], b=[t.lower() for t in b], autojunk=False
    )
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        if (i2 - i1) > max_span or (j2 - j1) > max_span:
            continue
        src = " ".join(a[i1:i2]).strip()
        dst = " ".join(b[j1:j2]).strip()
        if not src or not dst:
            continue
        if src.lower() == dst.lower():
            continue
        # Разница только в «е/ё» — не ошибка распознавания, а выбор по смыслу:
        # «все» и «всё» разные слова. Слепой словарь тут не помощник, решает
        # корректор, он видит соседние слова. Подробности — в fixes.py::add.
        if yo_key(src) == yo_key(dst):
            continue
        if not has_letters(src) or not has_letters(dst):
            continue
        if len(src) < 3:
            continue
        if all(w.lower() in FILLERS for w in a[i1:i2]):
            continue
        out.append((src, dst))
    return out


class Learner:
    def __init__(self, cfg: dict, fixes, log_dir: Path, rec_dir: Path, cand_path: Path):
        lc = cfg.get("learn", {})
        self.enabled = bool(lc.get("enabled", True))
        self.promote_after = int(lc.get("promote_after", 2))
        self.keep_audio = bool(lc.get("keep_audio", True))
        self.fixes = fixes
        self.log_dir = log_dir
        self.rec_dir = rec_dir
        self.cand_path = cand_path
        self.candidates: dict[str, int] = {}
        if cand_path.exists():
            try:
                self.candidates = json.loads(cand_path.read_text(encoding="utf-8"))
            except Exception:
                self.candidates = {}

    def observe(self, raw: str, polished: str) -> list[tuple[str, str]]:
        """Считает правки модели. Возвращает пары, доросшие до словаря."""
        if not self.enabled or not raw or not polished:
            return []
        promoted = []
        for src, dst in candidate_pairs(raw, polished):
            key = f"{src.lower()}\t{dst}"
            self.candidates[key] = self.candidates.get(key, 0) + 1
            if self.candidates[key] >= self.promote_after:
                if self.fixes.add(src, dst, origin="auto"):
                    promoted.append((src, dst))
                self.candidates.pop(key, None)
        self._save_candidates()
        return promoted

    def _save_candidates(self) -> None:
        self.cand_path.parent.mkdir(parents=True, exist_ok=True)
        self.cand_path.write_text(
            json.dumps(self.candidates, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def log(self, record: dict) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m-%d")
        with open(self.log_dir / f"{day}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_audio(self, audio: np.ndarray, text: str, stamp: str) -> str | None:
        """Звук + текст парой — это материал для дообучения на твоём голосе."""
        if not self.keep_audio or audio.size == 0 or not text.strip():
            return None
        day = datetime.now().strftime("%Y-%m-%d")
        folder = self.rec_dir / day
        folder.mkdir(parents=True, exist_ok=True)
        wav_path = folder / f"{stamp}.wav"
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        (folder / f"{stamp}.txt").write_text(text.strip(), encoding="utf-8")
        return str(wav_path)
