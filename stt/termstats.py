# -*- coding: utf-8 -*-
"""Which of your words the recognizer gets told about.

The recognizer chokes on more than about 45 terms in its hint (see
[asr] prompt_terms), and glossary.txt holds 117. Until 31.08.2026 the 45 were
simply the first 45 lines of the file, ordered by hand — and the file itself
carries the scars: three separate notes about a word ("herdr", "Hetzner",
"CLI") that was being mangled every single time purely because it sat below
the line and never reached the hint.

Hand-ordering cannot keep up with what a person actually talks about. Measured
on 2994 takes from the fortnight to 31.08.2026:

    in the hint, not said once     Ноам, mailbox, Postgres, Supabase
    said a lot, not in the hint    autopase (116), Lavish (77), Fable (72),
                                   Mac (62), VPS (59), Hermes (49),
                                   Hostinger (34), Chrome Extension (20)

So the hint is now filled by how often each term is really spoken, counted
from the logs. A term that has been mangled all along still counts: every
known misrecognition of it (the left-hand side of fixes.tsv) is counted too,
otherwise a word the recognizer gets wrong would never appear in the logs,
never earn a place in the hint, and go on being wrong forever.

The first few lines of the file are kept regardless of the count — that is
where the people are, and a colleague's name has to be spelled right the first
time it is said, not after it has been said often enough.
"""
import collections
import json
import re
from pathlib import Path

WORD_RE = re.compile(r"[^\W\d_][\w.'-]*", re.UNICODE)

# The names of the people come first in glossary.txt and stay in the hint
# whatever the counts say: "Ноам" was heard as "Ноа" on 25.08.2026 and moved to
# the top by hand on 27.08 for exactly that reason, and he is not talked about
# every day.
KEEP_HEAD = 4


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def read_logs(log_dir: Path, days: int = 14) -> collections.Counter:
    """How often each word was said, over the last `days` daily logs.

    Both `final` and `raw` are counted: `raw` is what the recognizer actually
    heard, which is where a mangled term shows itself.
    """
    counts = collections.Counter()
    files = sorted(Path(log_dir).glob("????-??-??.jsonl"))[-days:]
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    counts.update(_words(rec.get("final") or ""))
                    counts.update(_words(rec.get("raw") or ""))
        except OSError:
            continue
    return counts


def _spoken(term: str, counts: collections.Counter, index: dict) -> int:
    """How often one term (or a declined form of it) appears in the counts.

    A term of several words scores as its RAREST word, not the sum: "Google
    Trends" is said when "Trends" is said, and counting every "Google" would
    have carried it into the hint over words said ten times more often (it did,
    in the first version of this).
    """
    parts = [p for p in term.lower().split() if len(p) >= 3]
    if not parts:
        return 0
    return min(index.get(p, 0) for p in parts)


def _prefix_index(counts: collections.Counter) -> dict:
    """Word counts folded onto their stems, so declensions count as the word.

    "автопасса", "автопассе", "автопас" all have to count towards "autopase" —
    a Russian speaker declines an English product name without thinking about
    it. Every prefix of every word from 3 letters up is summed once, which
    turns the lookup above into a dictionary hit instead of 117 regular
    expressions over two megabytes of text (2674 ms measured, against 60 ms
    this way).
    """
    index = collections.Counter()
    for word, n in counts.items():
        for size in range(3, len(word) + 1):
            index[word[:size]] += n
    return index


def rank(terms: list[str], log_dir: Path, aliases: dict | None = None,
         days: int = 14, skip: set | None = None) -> list[str]:
    """The terms, most-spoken first. Ties keep the order of the file.

    aliases: {term: {how it gets misheard, ...}} — usually built from fixes.tsv.
    skip:    terms whose count cannot be trusted because the word is also an
             ordinary Russian word ("Это" is a colleague AND the word "this",
             and no counting can tell them apart). They keep file order at the
             back rather than crowding out real terms.
    """
    counts = read_logs(Path(log_dir), days)
    if not counts:
        return list(terms)
    index = _prefix_index(counts)
    aliases = aliases or {}
    skip = {s.lower() for s in (skip or set())}
    score = {}
    for term in terms:
        if term.lower() in skip:
            score[term] = 0
            continue
        n = _spoken(term, counts, index)
        for alias in aliases.get(term.lower(), ()):
            n += _spoken(alias, counts, index)
        score[term] = n
    head = terms[:KEEP_HEAD]
    rest = sorted(
        range(KEEP_HEAD, len(terms)),
        key=lambda i: (-score[terms[i]], i),
    )
    return head + [terms[i] for i in rest]


def aliases_from_fixes(fixes) -> dict:
    """{term: {every known way it comes out wrong}} from the replacements table.

    A term that is always misheard never appears in the logs under its own
    name. Counting its misrecognitions is what lets it climb into the hint and
    stop being misheard.
    """
    out: dict[str, set] = {}
    try:
        # Fixes.pairs is {heard: (correct, times it helped, auto|manual)}.
        pairs = getattr(fixes, "pairs", None) or {}
        for src, value in dict(pairs).items():
            dst = value[0] if isinstance(value, (tuple, list)) else value
            out.setdefault(str(dst).lower(), set()).add(str(src).lower())
    except Exception:
        return out
    return out
