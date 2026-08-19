# -*- coding: utf-8 -*-
"""Storage for takes: logs, marks and corrections.

The logs (logs/*.jsonl) are written by the app itself and never touched again —
they are read-only. Everything you change by hand on the page lives separately
in state/marks.json: the "bad" mark and the corrected text.
"""
import json
import threading
from datetime import datetime
from pathlib import Path

from . import config as cfg_mod

MARKS_PATH = cfg_mod.ROOT / "state" / "marks.json"
_LOCK = threading.Lock()

# How many hours of your speech are needed to fine-tune the recognizer.
TRAINING_TARGET_HOURS = 2.5


def _load_marks() -> dict:
    if not MARKS_PATH.exists():
        return {}
    try:
        return json.loads(MARKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_marks(marks: dict) -> None:
    MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKS_PATH.write_text(
        json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _read_log_lines() -> list[dict]:
    out = []
    for lf in sorted(cfg_mod.LOG_DIR.glob("*.jsonl")):
        day = lf.stem
        for n, line in enumerate(lf.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # Old takes have no id of their own — build one from day + line.
            # No "#" in it: in a URL that means an anchor, and the audio link
            # would break.
            rec.setdefault("id", f"{day}_line{n:04d}")
            rec["day"] = day
            out.append(rec)
    return out


def records(limit: int = 200, query: str = "", only_bad: bool = False) -> list[dict]:
    """Takes, newest first, together with their marks and corrections."""
    marks = _load_marks()
    out = []
    for rec in reversed(_read_log_lines()):
        mark = marks.get(rec["id"], {})
        text = mark.get("corrected") or rec.get("final") or rec.get("raw") or ""
        if only_bad and not mark.get("bad"):
            continue
        if query:
            hay = f"{text} {rec.get('raw','')}".lower()
            if query.lower() not in hay:
                continue
        out.append(
            {
                "id": rec["id"],
                "time": rec.get("time", ""),
                "day": rec["day"],
                "seconds": rec.get("seconds_audio", 0),
                "text": text,
                "raw": rec.get("raw", ""),
                "final": rec.get("final", ""),
                "corrected": mark.get("corrected", ""),
                "bad": bool(mark.get("bad")),
                "note": rec.get("polish_note", ""),
                "ms_total": rec.get("ms_total", 0),
                "ms_asr": rec.get("ms_asr", 0),
                "ms_polish": rec.get("ms_polish", 0),
                "fixes": rec.get("fixes_applied", 0),
                "learned": rec.get("learned", []),
                "has_audio": bool(rec.get("wav")) and Path(rec["wav"]).exists(),
            }
        )
        if len(out) >= limit:
            break
    return out


def audio_path(rec_id: str):
    for rec in _read_log_lines():
        if rec.get("id") == rec_id:
            wav = rec.get("wav")
            if wav and Path(wav).exists():
                return Path(wav)
    return None


def set_bad(rec_id: str, bad: bool) -> dict:
    with _LOCK:
        marks = _load_marks()
        entry = marks.setdefault(rec_id, {})
        if bad:
            entry["bad"] = True
            entry["bad_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            entry.pop("bad", None)
            entry.pop("bad_at", None)
        if not entry:
            marks.pop(rec_id, None)
        _save_marks(marks)
    return {"id": rec_id, "bad": bad}


def set_text(rec_id: str, text: str, fixes=None) -> dict:
    """Saves your corrected text.

    It goes to two places: the page (so you copy the right thing) and the
    audio + text pair next to the recording — that is the fine-tuning material.
    The difference between what was heard and your correction also teaches the
    replacement dictionary.
    """
    text = text.strip()
    learned = []
    with _LOCK:
        marks = _load_marks()
        entry = marks.setdefault(rec_id, {})
        entry["corrected"] = text
        entry["corrected_at"] = datetime.now().isoformat(timespec="seconds")
        _save_marks(marks)

    for rec in _read_log_lines():
        if rec.get("id") != rec_id:
            continue
        wav = rec.get("wav")
        if wav and Path(wav).exists():
            Path(wav).with_suffix(".txt").write_text(text, encoding="utf-8")
        if fixes is not None and text:
            from .learn import candidate_pairs

            for src, dst in candidate_pairs(rec.get("raw", ""), text):
                if fixes.add(src, dst, origin="manual"):
                    learned.append([src, dst])
        break
    return {"id": rec_id, "text": text, "learned": learned}


def delete(rec_id: str) -> dict:
    """Removes a take from the list and deletes its audio from disk."""
    removed = False
    for rec in _read_log_lines():
        if rec.get("id") != rec_id:
            continue
        wav = rec.get("wav")
        if wav:
            for p in (Path(wav), Path(wav).with_suffix(".txt")):
                try:
                    p.unlink(missing_ok=True)
                    removed = True
                except Exception:
                    pass
        break
    with _LOCK:
        marks = _load_marks()
        marks.setdefault(rec_id, {})["deleted"] = True
        _save_marks(marks)
    return {"id": rec_id, "deleted": True, "audio_removed": removed}


def stats() -> dict:
    marks = _load_marks()
    recs = _read_log_lines()
    total_sec = sum(r.get("seconds_audio", 0) or 0 for r in recs)
    bad = sum(1 for m in marks.values() if m.get("bad"))
    corrected = sum(1 for m in marks.values() if m.get("corrected"))
    times = [r.get("ms_total", 0) for r in recs if r.get("ms_total")]
    times.sort()

    # Training material: takes that have audio and are not marked bad.
    train_sec = 0.0
    train_n = 0
    for r in recs:
        mark = marks.get(r.get("id"), {})
        if mark.get("bad") or mark.get("deleted"):
            continue
        wav = r.get("wav")
        if wav and Path(wav).exists():
            train_sec += r.get("seconds_audio", 0) or 0
            train_n += 1

    return {
        "records": len(recs),
        "minutes": round(total_sec / 60, 1),
        "bad": bad,
        "corrected": corrected,
        "median_ms": times[len(times) // 2] if times else 0,
        "worst_ms": times[-1] if times else 0,
        "train_records": train_n,
        "train_minutes": round(train_sec / 60, 1),
        "train_target_minutes": round(TRAINING_TARGET_HOURS * 60),
        "train_percent": min(100, round(train_sec / (TRAINING_TARGET_HOURS * 3600) * 100)),
    }


def add_terms(text: str) -> dict:
    """Adds terms at the very top of glossary.txt.

    At the top, because only the first ~45 lines are sent to the recognizer as
    a hint: whatever was just added is what is needed most.
    Several at once are fine — one per line or comma-separated.
    """
    wanted = []
    for chunk in text.replace(",", "\n").splitlines():
        term = chunk.strip()
        if term and not term.startswith("#"):
            wanted.append(term)
    if not wanted:
        return {"added": [], "already": []}

    lines = cfg_mod.GLOSSARY_PATH.read_text(encoding="utf-8").splitlines() \
        if cfg_mod.GLOSSARY_PATH.exists() else []
    have = {ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}

    added = [t for t in wanted if t.lower() not in have]
    already = [t for t in wanted if t.lower() in have]
    if not added:
        return {"added": [], "already": already}

    # Insert right after the comment header.
    head = 0
    for i, ln in enumerate(lines):
        if ln.strip() and not ln.startswith("#"):
            head = i
            break
    else:
        head = len(lines)

    lines[head:head] = added + [""]
    cfg_mod.GLOSSARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"added": added, "already": already}


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_file(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
