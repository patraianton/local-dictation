# -*- coding: utf-8 -*-
"""The words of the conversation a take is dictated into.

The recognizer is given about 45 terms, chosen by how often each was said over
two weeks of ALL dictation. But the owner works in a dozen windows at once, each
on its own project with its own words, and the 45 belong to whichever project
was loudest lately. On 23.10.2026 a whole evening on one project came out with
its key words misheard, «ICP» as «ACP» and «IBM», «IMO» as «АМО», «LLM» as
«лолемки»: ICP was in the glossary, but not among the 45.

When the take goes into a Claude window in herdr, herdr says which session it
is, and the session's transcript holds the conversation. The terms that
conversation uses go to the front of the hint for this one take.
"""
import collections
import json
import re
import time
from datetime import datetime
from pathlib import Path

# Of a transcript only the end is read: the recent conversation is what the
# take continues, and a long session's file runs to tens of megabytes.
TAIL_BYTES = 600_000
# The last this many messages, both sides.
MESSAGES = 60
# A word the conversation uses fewer times than this is not its word.
MIN_COUNT = 2

CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
URL_RE = re.compile(r"\bhttps?://\S+|\bwww\.\S+")
PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\\/-]+|\b[\w-]+\.(?:py|js|ts|tsx|md|json|toml|tsv|txt|ps1|sh|html|css|log|jsonl|yml|yaml)\b")
LATIN_RE = re.compile(r"(?<![\w.@/-])[A-Za-z][A-Za-z0-9]*(?:[.'’+-][A-Za-z0-9]+)*(?![\w@/])")
# Words of plain English that say nothing about the project, and markup.
STOP = set("""
a an and are as at be but by can do does for from has have how i if in into is it its
me my no not of on or our so that the their them then there these they this to up us
was we were what when where which who why will with you your yes ok okay all any just
like more most new now one only other out over see some than too use used very via
also back done good here let make need next same set still such sure take try want way
well work yet please thanks hello hi fine true false none null todo fyi etc vs eg ie
so-so http https www com org io ru lv html json md
""".split())


def session_file(session_id: str, roots: list[Path] | None = None) -> Path | None:
    """The transcript of a Claude session, wherever its home folder is."""
    if not session_id or not re.fullmatch(r"[0-9a-fA-F-]{8,}", session_id):
        return None
    if roots is None:
        home = Path.home()
        roots = [home / ".claude" / "projects"] + sorted(home.glob(".claude-accounts/*/projects"))
    for root in roots:
        for hit in root.glob(f"*/{session_id}.jsonl"):
            return hit
    return None


def _parse_ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _texts(record: dict) -> list[str]:
    """What a person reads in one record: the owner's words and the answers."""
    kind = record.get("type")
    if kind not in ("user", "assistant") or record.get("isSidechain") or record.get("isMeta"):
        return []
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return [] if content.lstrip().startswith("<") else [content]
    out = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text") or ""
            if not text.lstrip().startswith("<"):
                out.append(text)
    return out


def recent_text(path: Path, before: float | None = None, messages: int = MESSAGES) -> str:
    """The last messages of a transcript, written before `before` (a timestamp)."""
    # A take of the past (`before` given, a replay) may sit anywhere in the
    # file, so there the whole file is read; a live take reads only the end.
    tail = TAIL_BYTES if before is None else 0
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail) if tail else 0)
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = raw.splitlines()
    if tail and size > tail:
        lines = lines[1:]   # the first line is cut in half
    picked: list[str] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if before is not None:
            ts = _parse_ts(rec.get("timestamp"))
            if ts is None or ts >= before:
                continue
        texts = _texts(rec)
        if texts:
            picked.append("\n".join(texts))
            if len(picked) >= messages:
                break
    # A separator no message holds, so each can be judged on its own.
    return "\n\x1e\n".join(reversed(picked))


def _clean(text: str) -> str:
    text = CODE_BLOCK_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    return PATH_RE.sub(" ", text)


CYR_RE = re.compile(r"[А-Яа-яЁё]")
ANY_LETTER_RE = re.compile(r"[^\W\d_]")


def russian(text: str) -> bool:
    """Mostly Russian: there a word in Latin letters is a name or a term. In
    a message written in English every word is Latin and says nothing."""
    letters = len(ANY_LETTER_RE.findall(text))
    return letters > 0 and len(CYR_RE.findall(text)) >= 0.5 * letters


def terms_in(text: str, glossary: list[str], limit: int = 15, common=None) -> list[str]:
    """The conversation's own terms, most used first.

    Glossary terms it uses come first, spelled as in the glossary. Only those
    in Latin letters: the Cyrillic ones are people's names, already pinned at
    the head of the hint, and one of them ("Это") is also the commonest word
    there is. Then words in Latin letters that the glossary lacks — acronyms,
    names, product words — taken from Russian messages only. A word must turn
    up at least MIN_COUNT times; code, paths, links and words with digits
    (commit ids) are cut out before counting. `common(word)` tells a plain
    English word ("item", "fix", "DEMO"), which the recognizer writes right
    with no help: the hint's few places are for the rare ones.
    """
    # One message, or one paragraph of it, at a time: English inside a Russian
    # message is skipped on its own.
    parts = [_clean(p) for p in re.split(r"\n\s*\n|\x1e", text)]
    text = "\n".join(parts)
    low = text.lower()
    found: collections.Counter = collections.Counter()
    known: set[str] = set()
    for term in glossary:
        t = term.strip()
        if not t:
            continue
        known.add(t.lower())
        if not re.search(r"[A-Za-z]", t):
            continue
        n = len(re.findall(r"(?<![\w])" + re.escape(t.lower()) + r"(?![\w])", low))
        if n >= MIN_COUNT:
            found[t] = n
    glossary_hits = [t for t, _ in found.most_common()]
    spelled: dict[str, collections.Counter] = {}
    for part in parts:
        if not russian(part):
            continue
        for m in LATIN_RE.finditer(part):
            word = m.group(0).strip(".'’+-")
            key = word.lower()
            if len(word) < 2 or key in STOP or key in known or re.search(r"\d", word):
                continue
            if len(word) == 2 and not word.isupper():
                continue
            # A folder, a domain, a file or a slug ("annuities-genius",
            # "claude.ai", "Pages.csv") is written, not said.
            if re.search(r"[-._]", word):
                continue
            if common is not None and common(key):
                continue
            spelled.setdefault(key, collections.Counter())[word] += 1
    fresh = []
    for key, forms in spelled.items():
        n = sum(forms.values())
        if n >= MIN_COUNT:
            fresh.append((n, forms.most_common(1)[0][0]))
    fresh.sort(key=lambda x: -x[0])
    out = glossary_hits + [w for _, w in fresh if w.lower() not in {g.lower() for g in glossary_hits}]
    return out[:limit]


def merge(context: list[str], ranked: list[str], head: int, size: int,
          most: int = 10) -> list[str]:
    """The hint list for one take: the usual one, with the conversation's
    terms it lacks put in place of its last (least said) entries.

    The usual list is left as it is everywhere else. The recognizer is
    sensitive to its hint as a whole: on 24.10.2026, with the window's terms
    moved to the FRONT and the other 41 shifted, 51 of 220 takes read
    differently, many of them in words no term had to do with («Customer.io»,
    in both hints, came out as «а кастом району»). Terms the usual list
    already has change nothing; when none is new, the hint is the usual one.
    """
    base = [t for t in ranked[:size] if t]
    have = {t.lower() for t in base}
    new: list[str] = []
    for t in context:
        if t and t.lower() not in have and t.lower() not in {n.lower() for n in new}:
            new.append(t)
        if len(new) >= min(most, max(0, size - head)):
            break
    if not new:
        return base
    return base[:len(base) - len(new)] + new


def common_by(tokenizer):
    """A plain English word: one token of the recognizer's own vocabulary."""
    def common(word: str) -> bool:
        try:
            return len(tokenizer.encode(" " + word.lower(), add_special_tokens=False).ids) == 1
        except Exception:
            return False
    return common


class Context:
    """Finds the conversation of the window in front, with a small cache."""

    def __init__(self, glossary: list[str], limit: int = 15, common=None):
        self.glossary = glossary
        self.limit = limit
        self.common = common
        self._files: dict[str, Path | None] = {}
        self._cache: dict[str, tuple[float, float, list[str]]] = {}

    def terms(self, session_id: str, before: float | None = None) -> list[str]:
        if session_id not in self._files:
            self._files[session_id] = session_file(session_id)
        path = self._files[session_id]
        if path is None:
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        hit = self._cache.get(session_id)
        if before is None and hit and hit[0] == mtime and time.time() - hit[1] < 60:
            return hit[2]
        found = terms_in(recent_text(path, before), self.glossary, self.limit, self.common)
        if before is None:
            self._cache[session_id] = (mtime, time.time(), found)
        return found
