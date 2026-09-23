# -*- coding: utf-8 -*-
"""Learning from your own speech.

Three things:
1. A log of what was heard, what was pasted and how long it took
   (logs/YYYY-MM-DD.jsonl).
2. Audio + text pairs for fine-tuning the recognizer later (recordings/).
3. A self-growing dictionary: when the corrector fixes the same word again and
   again, it lands in fixes.tsv for good and is fixed instantly from then on.
"""
import difflib
import json
import re
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from . import endings
from .fixes import yo_key

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Filler words: dropping one is not a "recognition error", so never learn it.
FILLERS = {
    "э", "ээ", "эээ", "а", "аа", "ну", "вот", "типа", "как", "бы", "это",
    "самое", "значит", "короче", "так", "там", "то", "есть", "мм", "ммм", "угу",
}


# Punctuation that arranges a sentence rather than spelling a word. The
# corrector puts these next to a word because of its neighbours, so they
# must never be learned as part of the word itself. A dot or a hyphen
# INSIDE a word is fine — "customer.io", "large-v3-turbo" — which is why
# this list is not simply "everything that is not a letter". A full stop is
# absent on purpose: inside a replacement it is part of a domain, and at the
# edge it is caught by the check below instead.
STRUCTURE_PUNCT = {"—", "–", ",", ";", ":", "!", "?", "…"}


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def has_letters(s: str) -> bool:
    return bool(LETTER_RE.search(s))


def join(parts: list[str]) -> str:
    """Puts tokens back together the way they were written.

    A plain " ".join spaces out the punctuation inside a name: "team-ops"
    came back as "team - ops" and would have been learned that way. A space
    goes only between two words.
    """
    out = ""
    for i, t in enumerate(parts):
        if i and has_letters(t) and has_letters(parts[i - 1]):
            out += " "
        out += t
    return out


def candidate_pairs(raw: str, polished: str, max_span: int = 3) -> list[tuple[str, str]]:
    """What exactly the corrector rewrote. Word substitutions only."""
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
        src = join(a[i1:i2]).strip()
        dst = join(b[j1:j2]).strip()
        if not src or not dst:
            continue
        if src.lower() == dst.lower():
            continue
        # Differing only by "е"/"ё" is not a recognition error but a choice
        # of meaning: "все" and "всё" are different words. A blind dictionary
        # cannot help here; the corrector decides, because it sees the
        # neighbouring words. Details in fixes.py::add.
        if yo_key(src) == yo_key(dst):
            continue
        # Two forms of one verb are never a dictionary entry, in either
        # direction. An order and a promise differ by one ending — "сделай" and
        # "сделаю" — and only the sentence around them says which is right.
        # On 2026-08-25 the pair "делай -> делаю" got in exactly this way: the
        # corrector broke one phrase, the counter reached two, and from then on
        # the blind dictionary would have turned every order the owner dictated
        # into a report about himself, before the corrector even ran.
        if endings.touches_verb_form(src, dst):
            continue
        if not has_letters(src) or not has_letters(dst):
            continue
        # No punctuation the speaker did not say. On 22.08.2026 the pair
        # "все -> — всё" sat in the dictionary: the corrector had once put a
        # dash before "всё" because of the words around it, the pair was
        # learned with the dash inside, and from then on EVERY "все" the owner
        # said came out as "— всё". That is where "Там — всё по задачам", "и я
        # — всё время подозреваю" and the doubled "— — всё ресурсы" came from.
        new_punct = {t for t in b[j1:j2] if t in STRUCTURE_PUNCT}
        if new_punct - {t for t in a[i1:i2] if t in STRUCTURE_PUNCT}:
            continue
        # The same thing at the edges: a word never begins or ends with a mark.
        # BOTH sides are checked. The right one alone is not enough: a comma
        # stuck to the left side carries the pair past every other guard,
        # because "партнеров," is not the same string as "партнеров" and the
        # "е"/"ё" check above compares strings. The pair "партнеров, ->
        # партнёров" was found this way on 2026-08-25, one step from becoming
        # permanent; applied, it swallowed the comma: "У партнеров, которые
        # платят" came out as "У партнёров которые платят".
        if not has_letters(b[j1]) or not has_letters(b[j2 - 1]):
            continue
        if not has_letters(a[i1]) or not has_letters(a[i2 - 1]):
            continue
        if len(src) < 3:
            continue
        if all(w.lower() in FILLERS for w in a[i1:i2]):
            continue
        out.append((src, dst))
    return out


class Learner:
    def __init__(self, cfg: dict, fixes, log_dir: Path, rec_dir: Path,
                 cand_path: Path, terms: list[str] | None = None):
        lc = cfg.get("learn", {})
        self.enabled = bool(lc.get("enabled", True))
        self.promote_after = int(lc.get("promote_after", 2))
        self.keep_audio = bool(lc.get("keep_audio", True))
        self.fixes = fixes
        self.log_dir = log_dir
        self.rec_dir = rec_dir
        self.cand_path = cand_path
        self.terms = {t.strip().lower() for t in (terms or []) if t.strip()}
        self.candidates: dict[str, int] = {}
        # How many times the corrector has fixed ANYTHING into this term.
        # See observe() for why the pair alone is not enough.
        self.by_target: dict[str, int] = {}
        if cand_path.exists():
            try:
                saved = json.loads(cand_path.read_text(encoding="utf-8"))
            except Exception:
                saved = {}
            if isinstance(saved, dict) and "pairs" in saved:
                self.candidates = dict(saved.get("pairs") or {})
                self.by_target = dict(saved.get("targets") or {})
            else:                       # the old flat format
                self.candidates = dict(saved or {})
                # Sightings already collected are not thrown away: "автопассе",
                # "аутопаса", "автопаз" were three separate one-off candidates
                # and together they are three sightings of autopase.
                for key in self.candidates:
                    _, _, dst = key.partition("\t")
                    target = dst.strip().lower()
                    if target and target in self.terms:
                        self.by_target[target] = (
                            self.by_target.get(target, 0) + self.candidates[key]
                        )

    def observe(self, raw: str, polished: str) -> list[tuple[str, str]]:
        """Counts the model's edits. Returns pairs that graduated into the dictionary."""
        if not self.enabled or not raw or not polished:
            return []
        promoted = []
        # One take counts once. Without dict.fromkeys a single phrase where the
        # corrector made the same edit twice ("...в лавише... не в лавише...")
        # takes the pair straight from first sighting to permanent, and the
        # owner never gets a chance to see it happen.
        for src, dst in dict.fromkeys(candidate_pairs(raw, polished)):
            key = f"{src.lower()}\t{dst}"
            self.candidates[key] = self.candidates.get(key, 0) + 1
            seen = self.candidates[key]
            target = dst.strip().lower()
            known = target in self.terms
            if known:
                self.by_target[target] = self.by_target.get(target, 0) + 1
                # A name is mangled a different way every time. state/candidates
                # on 31.08.2026 held "автопассе", "аутопаса", "автопаз",
                # "автопасса" — four spellings of autopase, one sighting each,
                # so the counter never reached two and NONE of them was ever
                # learned. The same for Vercel ("павершели", "версель",
                # "версале", "верселя") and Herdr ("хердере", "хёрдере",
                # "хордер"). Counting per pair therefore never learns exactly
                # the words that need learning most.
                #
                # So a term from the glossary counts by the term: once the
                # corrector has fixed something into "autopase" twice, the next
                # mangling of it is learned on sight. It is a name in the
                # glossary being restored, not a word of the language being
                # rewritten — the risk the two-sighting rule guards against is
                # not the same risk here.
                seen = max(seen, self.by_target[target])
            if seen >= self.promote_after:
                if self.fixes.add(src, dst, origin="auto"):
                    promoted.append((src, dst))
                self.candidates.pop(key, None)
        self._save_candidates()
        return promoted

    def _save_candidates(self) -> None:
        self.cand_path.parent.mkdir(parents=True, exist_ok=True)
        self.cand_path.write_text(
            json.dumps({"pairs": self.candidates, "targets": self.by_target},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def log(self, record: dict) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m-%d")
        with open(self.log_dir / f"{day}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_audio(self, audio: np.ndarray, text: str, stamp: str) -> str | None:
        """An audio + text pair: the material for fine-tuning on your voice."""
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
