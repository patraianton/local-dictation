# -*- coding: utf-8 -*-
"""Хранилище диктовок: журналы, пометки и правки.

Журналы (logs/*.jsonl) пишет сама диктовка и больше не трогает — они только
для чтения. Всё, что ты меняешь руками на странице, лежит отдельно, в
state/marks.json: пометка «плохо» и исправленный текст.
"""
import json
import threading
from datetime import datetime
from pathlib import Path

from . import config as cfg_mod

MARKS_PATH = cfg_mod.ROOT / "state" / "marks.json"
_LOCK = threading.Lock()

# Сколько часов твоей речи нужно, чтобы дообучать распознавалку под твой голос.
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
            # У старых записей нет своего номера — собираем из дня и строки.
            # Без «#»: в адресе он означает якорь, и звук по такой ссылке теряется.
            rec.setdefault("id", f"{day}_line{n:04d}")
            rec["day"] = day
            out.append(rec)
    return out


def records(limit: int = 200, query: str = "", only_bad: bool = False) -> list[dict]:
    """Диктовки, новые сверху, вместе с пометками и правками."""
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
    """Сохраняет твой правильный текст.

    Он идёт в два места: на страницу (чтобы копировать уже верное) и в пару
    «звук + текст» рядом с записью — это и есть материал для дообучения.
    Заодно разница между услышанным и твоей правкой учит словарь.
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
    """Убирает запись из списка и стирает звук с диска."""
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

    # Материал для дообучения: записи со звуком, не помеченные как плохие.
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
    """Добавляет термины в самый верх glossary.txt.

    Наверх — потому что в подсказку распознавалке уходят только первые ~45
    строк: то, что добавлено сейчас, нужнее всего.
    Можно вписать сразу несколько — по одному в строке или через запятую.
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

    # Вставляем сразу после шапки из комментариев.
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
