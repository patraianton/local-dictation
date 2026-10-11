# -*- coding: utf-8 -*-
"""What was pasted against what was actually sent.

Most dictation goes into Claude Code windows, and every message the owner
sends there is written to a session transcript (~/.claude*/projects/*/*.jsonl).
Finding each take inside the message that went out shows every correction made
by hand after the paste: a word the recognizer got wrong, a word the corrector
broke, a piece deleted. That is the one honest measure of how good dictation
is — "nothing had to be fixed" — and the list of what to fix next.

A take is matched to the human message sent within an hour after it whose
words contain it best. Typed text before or after the take, or several takes
pasted into one message, do not matter: only the stretch of the message that
lines up with the take is compared. Case, punctuation and "ё"/"е" are ignored
in the comparison; only words count.

    python -m stt.sentcheck                 # today
    python -m stt.sentcheck --days 7        # the last week, with a summary
"""
import argparse
import bisect
import difflib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
# Wrappers the harness puts into the user role: none of them is typed by a human.
NOT_HUMAN_RE = re.compile(
    r"^\s*<(task-notification|system-reminder|command-name|command-message|"
    r"local-command|bash-|user-memory|persisted-output)", re.I)
# What the chat window puts in place of a pasted picture or a long pasted
# text: "[Image #1]". The owner did not say it, so it is no word of the take.
PLACEHOLDER_RE = re.compile(r"\[(?:Image|Pasted text) #\d+[^\]]*\]?")
ANSWER_RE = re.compile(r'"=(?:")?(.*?)"\.(?: Read the answers|\s*$)', re.S)

# A take counts as found when this share of its words lines up with a message.
FOUND_AT = 0.5
# At this share of the take's words changed it was said again, not corrected.
REDICTATED_AT = 0.4
# Words that match in almost any message: proof of nothing when nothing else
# of the take is there.
EVERYDAY = set("""
я ты он она оно мы вы они меня мне мной тебя тебе тобой его него ее ему ей
нас нам вас вам их им это этот эта эти то тот та те что чего как так вот ну да
нет не ни и а но или в во на с со у к ко о об от до по за из для при без же бы
ли уже еще тоже там тут здесь был была были было есть все просто типа
""".split())
# How long after a take its message may still be sent.
WINDOW = timedelta(minutes=60)


def default_roots() -> list[Path]:
    home = Path.home()
    roots = [home / ".claude" / "projects"]
    roots += sorted(home.glob(".claude-accounts/*/projects"))
    return [r for r in roots if r.is_dir()]


def norm(word: str) -> str:
    return word.lower().replace("ё", "е").strip("-'’")


def words(text: str) -> list[str]:
    return [w for w in (norm(m.group(0)) for m in WORD_RE.finditer(text or "")) if w]


def parse_ts(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def take_time(take_id: str):
    try:
        return datetime.strptime(take_id[:17], "%Y-%m-%d_%H%M%S").astimezone()
    except ValueError:
        return None


def human_texts(record: dict) -> list[str]:
    """The texts a human typed or pasted in one transcript record, if any."""
    kind = record.get("type")
    if kind == "attachment":
        att = record.get("attachment") or {}
        if att.get("type") == "queued_command" and (att.get("origin") or {}).get("kind") == "human":
            prompt = att.get("prompt") or ""
            if isinstance(prompt, list):
                prompt = " ".join(p.get("text", "") for p in prompt if isinstance(p, dict))
            return [prompt] if isinstance(prompt, str) else []
        return []
    # A summary written when a long conversation is compacted is stored as a
    # user record too, and it quotes the owner's earlier words: matched as a
    # sent message it reported a take as said again.
    if (kind != "user" or record.get("isMeta") or record.get("isSidechain")
            or record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly")):
        return []
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return [] if NOT_HUMAN_RE.match(content) else [content]
    out = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text") or ""
            if not NOT_HUMAN_RE.match(text):
                out.append(text)
        elif part.get("type") == "tool_result":
            # The answer to a question asked with a choice of options: the
            # owner may dictate a whole message into its "Other" field.
            body = part.get("content")
            if isinstance(body, list):
                body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
            if isinstance(body, str) and body.startswith("The user answered"):
                out += [m.group(1) for m in ANSWER_RE.finditer(body)]
    return out


class Inbox:
    """Human messages from all transcripts, read incrementally."""

    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots if roots is not None else default_roots()
        self.offsets: dict[Path, int] = {}
        self.messages: list[tuple[datetime, str, str]] = []  # (utc time, text, session file)
        # Listing tens of thousands of old transcripts takes about a second, so
        # the full list is refreshed every few minutes; in between only the
        # files that were alive lately are looked at.
        self._listed_at = 0.0
        self._live: set[Path] = set()

    def poll(self, since: datetime) -> None:
        """Reads everything new in transcripts touched after `since`."""
        cutoff = since.timestamp()
        now = time.time()
        if now - self._listed_at > 300:
            self._listed_at = now
            listed = [p for root in self.roots for p in root.glob("*/*.jsonl")]
        else:
            listed = list(self._live)
        for path in listed:
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            self._live.add(path)
            start = self.offsets.get(path, 0)
            if st.st_size < start:
                start = 0
            if st.st_size == start:
                continue
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read()
            end = data.rfind(b"\n") + 1  # a half-written last line waits
            self.offsets[path] = start + end
            for line in data[:end].decode("utf-8", errors="replace").splitlines():
                if '"user"' not in line and "queued_command" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                when = parse_ts(rec.get("timestamp", ""))
                if when is None:
                    continue
                for text in human_texts(rec):
                    text = PLACEHOLDER_RE.sub(" ", text)
                    if text.strip():
                        self.messages.append((when, text, path.stem))
        self.messages.sort(key=lambda m: m[0])


def locate(pasted: str, message: str):
    """(share of the take's words found, the stretch of the message that holds it, hunks).

    Hunks are (kind, pasted words, sent words) for every place the two differ.
    """
    a = words(pasted)
    if not a:
        return 0.0, "", []
    toks = list(WORD_RE.finditer(message or ""))
    b = [norm(t.group(0)) for t in toks]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    blocks = [blk for blk in sm.get_matching_blocks() if blk.size]
    if not blocks:
        return 0.0, "", []
    # In a long message built from several takes, a short word of this take
    # ("с", "и") also matches far away, and the stretch between ran over the
    # neighbouring takes: they were reported as hand edits (07.10.2026). Keep
    # only the matches that sit around the longest one, within the take's own
    # length plus some slack.
    anchor = max(blocks, key=lambda blk: blk.size)
    slack = max(3, len(a) // 2)
    lo_b = anchor.b - anchor.a - slack
    hi_b = anchor.b + anchor.size + (len(a) - anchor.a - anchor.size) + slack
    blocks = [blk for blk in blocks if blk.b >= lo_b and blk.b + blk.size <= hi_b]
    matched = sum(blk.size for blk in blocks)
    # The stretch of the message that lines up with the take, widened by the
    # words of the take that did not match at either end.
    lo = max(0, blocks[0].b - blocks[0].a)
    hi = min(len(b), blocks[-1].b + blocks[-1].size + (len(a) - blocks[-1].a - blocks[-1].size))
    region = b[lo:hi]
    hunks = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=region, autojunk=False).get_opcodes():
        if op != "equal":
            hunks.append((op, " ".join(a[i1:i2]), " ".join(region[j1:j2])))
    span = message[toks[lo].start():toks[hi - 1].end()] if hi > lo else ""
    return matched / len(a), span, hunks


def source_of(hunk: tuple[str, str, str], raw: str, after_fixes: str) -> str:
    """Who put the wrong words there: the recognizer, the dictionary or the corrector."""
    _op, _pasted, sent = hunk
    if not sent:
        return "deleted"
    sent_w = sent.split()
    for name, text in (("corrector", raw), ("dictionary", after_fixes)):
        w = words(text)
        n = len(sent_w)
        if any(w[i:i + n] == sent_w for i in range(len(w) - n + 1)):
            # The right words were there before this step and got lost in it.
            return name
    return "recognizer"


QUOTED_RE = re.compile(r"[«“]([^«»“”]+)(?:[»”]|$)")


def quotes_the_take(pasted: str, message: str) -> bool:
    """The message quotes the take in «…» instead of being the take.

    A window that writes to another window on the owner's behalf is a "user"
    there too, and it quotes his words: "the owner asks: «…»" was reported as
    a hand edit twice on 10.10.2026, the words outside the quotes taken for
    his corrections. A quote that is (nearly) all one stretch of the take, or that
    holds (nearly) all of it, says so. The recognizer writes no quotes; a take
    that has quotes of its own (the corrector put them in) is not judged here.
    """
    if any(c in pasted for c in "«»“”"):
        return False
    a = words(pasted)
    for m in QUOTED_RE.finditer(message or ""):
        q = words(m.group(1))
        if len(q) < 3:
            continue
        run = difflib.SequenceMatcher(a=a, b=q, autojunk=False).find_longest_match(
            0, len(a), 0, len(q)).size
        if run >= 3 and (run >= 0.8 * len(q) or run >= 0.8 * len(a)):
            return True
    return False


# How soon after a take the same thing said again counts as its replacement.
SAID_AGAIN_S = 120


def said_again_after(when, span: str, message: str, later) -> bool:
    """A take of the next two minutes holds (most of) the same stretch of this message.

    The owner broke a take off and said it all again at once: the message is
    the second take, and the first, matched into its opening words, was
    reported as corrected by hand (10.10.2026). Two takes pasted one after the
    other into one message hold different stretches of it and are not this.
    """
    if when is None or not span or not message:
        return False
    a0 = message.find(span)
    if a0 < 0:
        return False
    a1 = a0 + len(span)
    for nxt in later or ():
        t = take_time(str(nxt.get("id", "")))
        # A take is stamped when it ends; a long one said again began right
        # after this one but ended minutes later (a 95 s take, 11.10.2026).
        # Its audio also holds a moment from before the key was pressed.
        if t is not None:
            t -= timedelta(seconds=float(nxt.get("seconds_audio") or 0))
        if t is None or not when - timedelta(seconds=2) < t <= when + timedelta(seconds=SAID_AGAIN_S):
            continue
        score, span2, _ = locate(nxt.get("final") or nxt.get("raw") or "", message)
        b0 = message.find(span2) if span2 else -1
        if score < FOUND_AT or b0 < 0:
            continue
        shared = min(a1, b0 + len(span2)) - max(a0, b0)
        if shared >= 0.5 * (a1 - a0):
            return True
    return False


def check(take: dict, inbox: Inbox, later=()) -> dict:
    """Finds the message a take went into and what was changed in it by hand.

    `later`: the takes that came after this one, if known (see said_again_after).
    """
    pasted = take.get("final") or take.get("raw") or ""
    when = take_time(str(take.get("id", "")))
    best = (0.0, "", [], None, "", "")
    if when is not None:
        lo, hi = when - timedelta(seconds=5), when + WINDOW
        first = bisect.bisect_left(inbox.messages, lo, key=lambda m: m[0])
        for ts, text, session in inbox.messages[first:]:
            if ts > hi:
                break
            score, span, hunks = locate(pasted, text)
            if score > best[0]:
                best = (score, span, hunks, ts, session, text)
    score, span, hunks, ts, session, message = best
    out = {"id": take.get("id"), "pasted": pasted, "score": round(score, 2)}
    if score < FOUND_AT:
        out["status"] = "not_found"
        return out
    if hunks and quotes_the_take(pasted, message):
        out["status"] = "quoted"
        return out
    # A large share changed is not a correction of the take but the take said
    # again: the owner dropped it and dictated the thought anew, and this
    # message holds the new take. Still a take he was not happy with.
    # One shared word is no proof the take is in this message: "Ну, например"
    # was reported as edited into a typed "идей? например" (07.10.2026).
    if hunks and len(words(pasted)) - sum(len(h[1].split()) for h in hunks) < 2:
        out["status"] = "not_found"
        return out
    # Nor are two tiny words: "Я не помню" was reported as edited into a later
    # message that only had "я даже не поеду" in it (09.10.2026).
    if hunks:
        kept = Counter(words(pasted)) - Counter(w for h in hunks for w in h[1].split())
        if all(len(w) <= 2 for w in kept):
            out["status"] = "not_found"
            return out
        # Nor are everyday words that turn up in any long text: "У меня есть"
        # was reported as edited into a typed letter of 300 words that had
        # "у меня нет" in it (11.10.2026).
        if all(w in EVERYDAY for w in kept):
            out["status"] = "not_found"
            return out
    changed = sum(max(len(h[1].split()), len(h[2].split())) for h in hunks)
    redone = changed >= 3 and changed >= REDICTATED_AT * len(words(pasted))
    # A take said again keeps a phrase of at least two words in a row. Single
    # common words scattered over a typed message are not that: "А он больше
    # ничего не писал?" was reported as said again in "именно он собирается.
    # Ничего клиентам не отправляй" — "он", "ничего", "не" (08.10.2026).
    if redone:
        a, b = words(pasted), words(span)
        run = difflib.SequenceMatcher(a=a, b=b, autojunk=False).find_longest_match(
            0, len(a), 0, len(b)).size
        if run < 2:
            out["status"] = "not_found"
            return out
    if hunks and said_again_after(when, span, message, later):
        out["status"] = "superseded"
        return out
    status = "same" if not hunks else "redictated" if redone else "edited"
    out.update(
        status=status,
        sent=span,
        sent_at=ts.isoformat() if ts else None,
        session=session,
        hunks=[
            {"kind": h[0], "pasted": h[1], "sent": h[2],
             "source": source_of(h, take.get("raw", ""), take.get("after_fixes", ""))}
            for h in hunks
        ],
    )
    return out


def describe(res: dict) -> str:
    """One line: every hand change as «pasted» → «sent» (who got it wrong)."""
    return "; ".join(f"«{h['pasted']}» → «{h['sent']}» ({h['source']})" for h in res.get("hunks", []))


def load_takes(logs: Path, since_id: str) -> list[dict]:
    out = []
    for f in sorted(logs.glob("????-??-??.jsonl")):
        if f.stem < since_id[:10]:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("id", "")) >= since_id:
                out.append(r)
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=float, default=0, help="look back this many days (0 = today)")
    ap.add_argument("--logs", default=str(Path(__file__).resolve().parent.parent / "logs"))
    ap.add_argument("--json", action="store_true", help="one JSON line per take")
    args = ap.parse_args(argv)
    start = datetime.now() - timedelta(days=args.days) if args.days else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    takes = load_takes(Path(args.logs), start.strftime("%Y-%m-%d_%H%M%S"))
    inbox = Inbox()
    inbox.poll(start.astimezone(timezone.utc))
    counts = {"same": 0, "edited": 0, "redictated": 0, "not_found": 0, "quoted": 0,
              "superseded": 0}
    for i, take in enumerate(takes):
        res = check(take, inbox, later=takes[i + 1:i + 6])
        counts[res["status"]] += 1
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        elif res["status"] in ("edited", "redictated"):
            print(f"{res['id']}  {res['status']}: {describe(res)}")
    found = len(takes) - counts["not_found"]
    print(f"\n{len(takes)} takes: sent unchanged {counts['same']}, edited by hand {counts['edited']},"
          f" said again {counts['redictated']}, quoted by another window {counts['quoted']},"
          f" broken off and said again in the next take {counts['superseded']}"
          f" (of {found} found), not found {counts['not_found']}",
          file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
