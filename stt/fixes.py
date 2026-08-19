# -*- coding: utf-8 -*-
"""Replacement dictionary: "what it heard" -> "what is correct".

File fixes.tsv, four TAB-separated columns:
    heard <TAB> correct <TAB> times it helped <TAB> auto|manual
It grows on its own (see learn.py) and by hand — just append a line.
"""
import re
import threading
from pathlib import Path


# A replacement starting with a lowercase Cyrillic letter is an ordinary word,
# not a name that carries its own spelling.
CYRILLIC_LOWER_RE = re.compile(r"[а-яё]", re.UNICODE)


def yo_key(word: str) -> str:
    """"ё" and "е" are two spellings of one letter, not two different words."""
    return word.lower().replace("ё", "е")


class Fixes:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.pairs: dict[str, tuple[str, int, str]] = {}
        self._regex = None
        self.load()

    def load(self) -> None:
        pairs: dict[str, tuple[str, int, str]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                src, dst = parts[0].strip(), parts[1].strip()
                hits = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                origin = parts[3].strip() if len(parts) > 3 else "manual"
                if src and dst:
                    pairs[src.lower()] = (dst, hits, origin)
        with self._lock:
            self.pairs = pairs
            self._rebuild()

    def _rebuild(self) -> None:
        if not self.pairs:
            self._regex = None
            return
        # Longest first, otherwise a short match eats a longer one.
        keys = sorted(self.pairs, key=len, reverse=True)
        body = "|".join(re.escape(k) for k in keys)
        self._regex = re.compile(rf"(?<!\w)({body})(?!\w)", re.IGNORECASE | re.UNICODE)

    def apply(self, text: str) -> tuple[str, int]:
        with self._lock:
            regex, pairs = self._regex, dict(self.pairs)
        if not regex or not text:
            return text, 0
        count = 0

        def sub(m):
            nonlocal count
            count += 1
            said = m.group(1)
            dst = pairs[said.lower()][0]
            # Case comes from what was heard. Replacements are stored lowercase,
            # but the recognizer capitalizes the first word of a sentence:
            # without this, "Все, закончили." came out as "все, закончили.".
            #
            # Ordinary words only. Names carry their own spelling and it wins:
            # "herdr" and "worktree" are always lowercase, "iPhone" stays as
            # written. The corrector capitalizes them at a sentence start — it
            # is the one that can see where a sentence begins.
            if said[:1].isupper() and CYRILLIC_LOWER_RE.match(dst):
                dst = dst[:1].upper() + dst[1:]
            return dst

        return regex.sub(sub, text), count

    def add(self, src: str, dst: str, origin: str = "auto") -> bool:
        """Adds a pair. Returns True if the pair is new.

        Two kinds of pair are refused outright — both have corrupted text before:

        - the two sides differ only by "е"/"ё" AND the machine proposed it.
          "все" (everybody) and "всё" (everything) are different words; swapping
          them blindly produced "на всё статьи", "всё картинки", "всё звонки".
          Only the corrector can decide, because it sees the neighbouring words.
          A human may still add such a pair by hand.
        - the reverse pair already exists. This happened: "все -> всё" AND
          "всё -> все" sat side by side and fought, breaking text both ways.
        """
        src, dst = src.strip(), dst.strip()
        if not src or not dst or src.lower() == dst.lower():
            return False
        key = src.lower()
        if origin == "auto" and yo_key(key) == yo_key(dst):
            return False
        with self._lock:
            back = self.pairs.get(dst.lower())
            if back and back[0].lower() == key:
                return False
            if key in self.pairs:
                old_dst, hits, old_origin = self.pairs[key]
                self.pairs[key] = (dst, hits + 1, old_origin)
                new = False
            else:
                self.pairs[key] = (dst, 1, origin)
                new = True
            self._rebuild()
            self._save_locked()
        return new

    def _save_locked(self) -> None:
        lines = [
            "# heard\tcorrect\ttimes used\tsource",
            "# Safe to edit by hand. Picked up live, no restart needed.",
        ]
        for key in sorted(self.pairs):
            dst, hits, origin = self.pairs[key]
            lines.append(f"{key}\t{dst}\t{hits}\t{origin}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def __len__(self) -> int:
        return len(self.pairs)
