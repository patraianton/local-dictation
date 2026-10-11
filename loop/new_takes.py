# -*- coding: utf-8 -*-
"""The improvement loop: what happened since the last reviewed take.

    python loop/new_takes.py            # new takes, dropped takes, alarms in service.log
    python loop/new_takes.py --mark     # after the review: remember the newest take as seen
    python loop/new_takes.py --since 2026-10-01_090000-000   # start from a given take

Reads the app's journals in logs/ and never writes there. Its own place is
state/loop-new-takes.json.
"""
import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
STATE = APP / "state" / "loop-new-takes.json"
ALARM = re.compile(
    r"FAILED|corrector OFF|skipping|still being processed|GPU|safety net|xrun|"
    r"stock subtitle|did not open|silence:|Traceback|Error|READY|had to wait|resumed",
    re.IGNORECASE,
)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def rows(folder: Path, since: str) -> list[dict]:
    out = []
    for f in sorted(folder.glob("????-??-??.jsonl"))[-3:]:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("id", "")) > since:
                out.append(r)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    st = load_state()
    since = st.get("last_id", "")
    if "--since" in args:
        since = args[args.index("--since") + 1]
    takes = rows(APP / "logs", since)
    dropped = rows(APP / "logs" / "dropped", since) if (APP / "logs" / "dropped").exists() else []

    if "--mark" in args:
        newest = max([since] + [str(r.get("id", "")) for r in takes + dropped])
        st["last_id"] = newest
        log = APP / "logs" / "service.log"
        st["log_offset"] = log.stat().st_size if log.exists() else 0
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"marked up to {newest}")
        return

    print(f"since {since or '(start)'}: {len(takes)} takes, {len(dropped)} dropped")
    for r in takes:
        head = (f"{r['id']} {r.get('seconds_audio', 0):5.1f}s {r.get('asr_model', '')[:12]:12} "
                f"pre={r.get('preroll_s', '')} note={r.get('polish_note', '')!r}"
                f" q={r.get('sound_q')} xr={r.get('xruns', '')}")
        print(head)
        print(f"   RAW:   {r.get('raw', '')}")
        if r.get("final", "") != r.get("raw", ""):
            print(f"   FINAL: {r.get('final', '')}")
    for r in dropped:
        print(f"DROPPED {r.get('id')} {r.get('reason')} {r.get('seconds')}s raw={r.get('raw', '')!r}")

    log = APP / "logs" / "service.log"
    if log.exists():
        # service.log has no dates and starts over on every restart: it is
        # followed by byte offset, and a file shorter than that means a restart.
        data = log.read_bytes()
        off = int(st.get("log_offset", 0))
        if off > len(data):
            off = 0
            print("LOG (service.log is shorter than last time: the service restarted)")
        lines = data[off:].decode("utf-8", errors="replace").splitlines()
        hits = [line for line in lines if ALARM.search(line) and line[11:12] != " "]
        for line in hits[-40:]:
            print(f"LOG {line}")


if __name__ == "__main__":
    main()
