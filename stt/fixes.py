# -*- coding: utf-8 -*-
"""Replacement dictionary: "what it heard" -> "what is correct".

File fixes.tsv, four TAB-separated columns:
    heard <TAB> correct <TAB> times it helped <TAB> auto|manual
It grows on its own (see learn.py) and by hand — just append a line.
"""
import re
import threading
from pathlib import Path

from . import endings


# A replacement starting with a lowercase Cyrillic letter is an ordinary word,
# not a name that carries its own spelling.
CYRILLIC_LOWER_RE = re.compile(r"[а-яё]", re.UNICODE)


def sentence_start(text: str, pos: int) -> bool:
    """Nothing but spaces before pos, or the last mark before it ends a sentence."""
    before = text[:pos].rstrip()
    return not before or before[-1] in ".!?…\n"


def yo_key(word: str) -> str:
    """"ё" and "е" are two spellings of one letter, not two different words."""
    return word.lower().replace("ё", "е")


class Fixes:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.pairs: dict[str, tuple[str, int, str]] = {}
        self._regex = None
        self._stamp = None  # the file as it was when last read or written
        self.load()

    def _on_disk(self):
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def refresh(self) -> bool:
        """Re-reads the file if it changed on disk since it was last read.

        The file says it is safe to edit by hand and picked up live, and nothing
        re-read it: a pair written in by hand worked only from the next restart.
        Worse, the next pair the learner added wrote the file back from memory
        and erased every hand-written pair since the start — 42 of them were lost
        that way on 06–07.10.2026.
        """
        if self._on_disk() == self._stamp:
            return False
        self.load()
        return True

    def load(self) -> None:
        stamp = self._on_disk()
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
            self._stamp = stamp
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
        try:
            self.refresh()
        except Exception:  # an unreadable file mid-save: use what we have
            pass
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
            #
            # A capital in the middle of a sentence says nothing about the
            # replacement either: the recognizer writes English words with a
            # capital anywhere ("Royce, Net" for "рой Sonnet") and takes a
            # mangled Russian word for a name ("отправь Ройсона от агентов" for
            # "рой Sonnet-агентов", 05.10.2026). So the capital comes from the
            # position, not from what was heard.
            if (said[:1].isupper() and CYRILLIC_LOWER_RE.match(dst)
                    and sentence_start(m.string, m.start())):
                dst = dst[:1].upper() + dst[1:]
            return dst

        # Runs on the raw text and again on the corrector's: neither should
        # keep a capital after a comma on an ordinary word (endings.comma_capital).
        return endings.comma_capital(regex.sub(sub, text)), count

    def add(self, src: str, dst: str, origin: str = "auto") -> bool:
        """Adds a pair. Returns True if the pair is new.

        Four kinds of pair are refused outright — all of them corrupted text before:

        - the two sides differ only by "е"/"ё" AND the machine proposed it.
          "все" (everybody) and "всё" (everything) are different words; swapping
          them blindly produced "на всё статьи", "всё картинки", "всё звонки".
          Only the corrector can decide, because it sees the neighbouring words.
          A human may still add such a pair by hand.
        - the reverse pair already exists. This happened: "все -> всё" AND
          "всё -> все" sat side by side and fought, breaking text both ways.
        - the two sides are forms of one verb. An order and a promise differ by
          one ending, and only the sentence around them says which is right. On
          2026-08-25 "делай -> делаю" got in and every order the owner dictated
          would have come out as a report about himself.
        - the left side is an ordinary Russian verb. A verb is never a mangled
          foreign term: "сесть -> сессия" turned "самое время сесть нам с тобой"
          into "самое время сессия нам с тобой" on 2026-08-20.

        The last two hold for a hand-made pair as well. There is no phrasing of
        them that is right in every sentence, and a blind dictionary has only
        one sentence: all of them. Editing fixes.tsv in a text editor still
        works — that is the way out, and it is a deliberate one.
        """
        src, dst = src.strip(), dst.strip()
        if not src or not dst or src.lower() == dst.lower():
            return False
        key = src.lower()
        if origin == "auto" and yo_key(key) == yo_key(dst):
            return False
        if endings.touches_verb_form(src, dst) or endings.is_known_verb(src):
            return False
        # The file is written back whole below: start from what is on disk now.
        self.refresh()
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

    def suspect_pairs(self) -> list[tuple[str, str, str]]:
        """Pairs in the file that add() would refuse today.

        load() does not filter: a line written by hand, or left over from an
        older version, works exactly like any other. So the bans below can only
        be enforced at the door, and this is the way to see what is already
        inside. Reported at startup, never removed on its own — the file is the
        owner's.
        """
        out = []
        with self._lock:
            pairs = dict(self.pairs)
        for key, (dst, _hits, _origin) in sorted(pairs.items()):
            if endings.touches_verb_form(key, dst):
                out.append((key, dst, "two forms of one verb"))
            elif endings.is_known_verb(key):
                out.append((key, dst, "the left side is an ordinary Russian verb"))
            elif pairs.get(dst.lower(), ("",))[0].lower() == key:
                out.append((key, dst, "the reverse pair is in the file too"))
        return out

    def _save_locked(self) -> None:
        lines = [
            "# heard\tcorrect\ttimes used\tsource",
            "# Safe to edit by hand. Picked up live, no restart needed.",
        ]
        for key in sorted(self.pairs):
            dst, hits, origin = self.pairs[key]
            lines.append(f"{key}\t{dst}\t{hits}\t{origin}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._stamp = self._on_disk()

    def __len__(self) -> int:
        return len(self.pairs)
