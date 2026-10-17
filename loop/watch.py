# -*- coding: utf-8 -*-
"""Live watch of the dictation: one printed burst per pause in dictation.

Polls the take journals every 10 s. New takes are held until the speaker
pauses (newest take older than 30 s) or 6 piled up, then printed together, one
line each, so a burst of dictation wakes whoever reads this once. Dropped
takes and alarm lines of service.log are printed at once.

Every take is also looked up in the message that was actually sent
(stt.sentcheck): a take corrected by hand, or dropped and said again, is
printed as EDITED / REDICTATED with what changed. All verdicts go to
logs/sent/<day>.jsonl.

    python loop/watch.py       # start where the last watch stopped (within the hour)

Its own place is state/loop-watch.state and state/loop-watch.dropped.
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
from stt import sentcheck  # noqa: E402
MAX_TAKE_S = 300  # the app's max_seconds
ALARM = re.compile(r"corrector OFF|no chat model|Traceback|FAILED|GPU|safety net|did not open|Error|stopped delivering", re.I)


def rows(folder: Path, since: str) -> list[dict]:
    out = []
    for f in sorted(folder.glob("????-??-??.jsonl"))[-2:]:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("id", "")) > since:
                out.append(r)
    return out


def age(rid: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(rid[:17], "%Y-%m-%d_%H%M%S")).total_seconds()
    except ValueError:
        return 1e9


def sent_check(inbox, open_takes: dict) -> None:
    """Finds each open take in the sent messages; reports hand corrections."""
    inbox.poll(datetime.now().astimezone() - sentcheck.WINDOW - timedelta(minutes=5))
    for rid in sorted(open_takes):
        if age(rid) < 60:
            continue
        later = rows(APP / "logs", rid)[:5]
        res = sentcheck.check(open_takes[rid], inbox, later=later)
        if res["status"] == "not_found" and age(rid) < sentcheck.WINDOW.total_seconds():
            continue
        # An edit may yet turn out to be a take broken off and said again:
        # give the next take time to come in, a long one included (it is
        # logged only when it ends, up to MAX_TAKE_S later).
        if res["status"] in ("edited", "redictated") and age(rid) < sentcheck.SAID_AGAIN_S + MAX_TAKE_S + 30:
            continue
        del open_takes[rid]
        out = APP / "logs" / "sent" / f"{rid[:10]}.jsonl"
        out.parent.mkdir(exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
        if res["status"] in ("edited", "redictated"):
            print(f"{res['status'].upper()} {rid}: {sentcheck.describe(res)} || SENT: {res.get('sent', '')}",
                  flush=True)


STATE = APP / "state" / "loop-watch.state"
# The last dropped take printed, kept apart: a drop is printed at once, before
# the takes still held for the pause, so it cannot share STATE with them.
DSTATE = APP / "state" / "loop-watch.dropped"
# Touched on every poll: when a watch last looked, whatever it printed.
ALIVE = APP / "state" / "loop-watch.alive"


def resume_point() -> str:
    """Where the previous watch stopped printing, if it was in the last hour.

    The watch is often run under a supervisor that stops it every 30 minutes
    and starts it again; without this, takes made in between were never
    printed or checked. "In the last hour" is when that watch last looked,
    not how old its last printed take is: after a quiet evening the last
    printed take was three hours old, the new watch started from "now", and
    the two takes said in the minute between the old watch stopping and the
    new one starting were never shown (16.10.2026)."""
    now = datetime.now().strftime("%Y-%m-%d_%H%M%S-999")
    try:
        last = STATE.read_text(encoding="utf-8").strip()
    except OSError:
        return now
    try:
        looked = time.time() - ALIVE.stat().st_mtime < 3600
    except OSError:
        looked = False
    return last if last and (looked or age(last) < 3600) else now


def unchecked(since: str) -> dict:
    """Takes of the last hour whose sent message has not been looked up yet."""
    hour_ago = (datetime.now() - sentcheck.WINDOW).strftime("%Y-%m-%d_%H%M%S")
    done = set()
    for f in (APP / "logs" / "sent").glob("*.jsonl"):
        if f.stem >= hour_ago[:10]:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    done.add(json.loads(line)["id"])
                except (ValueError, KeyError):
                    pass
    return {str(r["id"]): r for r in rows(APP / "logs", hour_ago)
            if str(r["id"]) not in done and str(r["id"]) <= since}


def main() -> None:
    STATE.parent.mkdir(exist_ok=True)
    since = resume_point()
    try:
        dsince = max(since, DSTATE.read_text(encoding="utf-8").strip())
    except OSError:
        dsince = since
    log = APP / "logs" / "service.log"
    off = log.stat().st_size if log.exists() else 0
    pending: list[dict] = []
    inbox = sentcheck.Inbox()
    open_takes: dict[str, dict] = unchecked(since)
    last_sent_poll = 0.0
    while True:
        try:
            new = rows(APP / "logs", since)
            if new:
                since = max(str(r["id"]) for r in new)
                pending += new
                for r in new:
                    open_takes[str(r["id"])] = r
            if open_takes and time.time() - last_sent_poll > 30:
                last_sent_poll = time.time()
                sent_check(inbox, open_takes)
            if pending and (age(pending[-1]["id"]) > 30 or len(pending) >= 6):
                for r in pending:
                    raw, fin = r.get("raw", ""), r.get("final", "")
                    tail = "" if fin == raw else f" || FINAL: {fin}"
                    print(f"TAKE {r['id']} {r.get('seconds_audio', 0):.1f}s [{r.get('polish_note', '')}] RAW: {raw}{tail}")
                sys.stdout.flush()
                STATE.write_text(str(pending[-1]["id"]), encoding="utf-8")
                pending = []
            dfolder = APP / "logs" / "dropped"
            if dfolder.exists():
                for r in rows(dfolder, dsince):
                    dsince = max(dsince, str(r.get("id", "")))
                    print(f"DROPPED {r.get('id')} {r.get('reason')} {r.get('seconds')}s", flush=True)
                    DSTATE.write_text(dsince, encoding="utf-8")
            if log.exists():
                size = log.stat().st_size
                if size < off:
                    off = 0
                    print("RESTARTED service.log started over", flush=True)
                if size > off:
                    data = log.read_bytes()[off:size].decode("utf-8", errors="replace")
                    off = size
                    for line in data.splitlines():
                        if ALARM.search(line) and "pasted via" not in line:
                            print(f"ALARM {line}", flush=True)
            ALIVE.touch()
        except Exception as exc:  # one bad read must not stop the watch
            print(f"WATCH-ERROR {type(exc).__name__}: {exc}", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
