# -*- coding: utf-8 -*-
"""The terms of the conversation a take is dictated into (24.10.2026).

A made-up transcript is written to a temporary folder; nothing real is read.

    ..\\.venv\\Scripts\\python.exe test_context.py
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.context import Context, merge, recent_text, session_file, terms_in  # noqa: E402

GLOSSARY = ["Анна", "Это", "PostHog", "Apollo", "ICP", "Vercel", "GitHub"]
COMMON = {"item", "fix", "main", "demo"}
SID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


def rec(kind, text, when):
    return {"type": kind, "timestamp": when, "message": {"content": [{"type": "text", "text": text}]}}


def transcript(folder: Path) -> Path:
    p = folder / "proj" / f"{SID}.jsonl"
    p.parent.mkdir(parents=True)
    lines = [
        rec("user", "Посмотри ICP по Apollo и сверь с PostHog.", "2026-10-23T10:00:00Z"),
        rec("assistant", "Сверил ICP: в Apollo 40 компаний, в PostHog события есть. Это IMO, "
                         "их сайт на Vercel. По IMO отдельный список.", "2026-10-23T10:01:00Z"),
        rec("assistant", "Here is the main item to fix: the demo build, the main item.", "2026-10-23T10:02:00Z"),
        rec("assistant", "Коммит a1b2c3d4 в `stt/asr.py`, ссылка https://example.com/IMO-page "
                         "и ещё раз a1b2c3d4.\n```\nconst IMO = PostHog\n```", "2026-10-23T10:03:00Z"),
        rec("user", "<system-reminder>PostHog PostHog</system-reminder>", "2026-10-23T10:04:00Z"),
        rec("assistant", "Позже: заводим LeRobot, снова LeRobot.", "2026-10-23T12:00:00Z"),
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return p


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    path = transcript(root)
    before = datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc).timestamp()
    early = recent_text(path, before)
    got = terms_in(early, GLOSSARY, common=COMMON.__contains__)

    @case("the session file is found by its id, a bad id finds nothing")
    def _():
        return session_file(SID, [root]) == path and session_file("../x", [root]) is None, ""

    @case("glossary terms first, most used first; then a new acronym")
    def _():
        return got[:3] == ["PostHog", "Apollo", "ICP"] and "IMO" in got, f"{got}"

    @case("a Cyrillic glossary word («Это») is never counted")
    def _():
        return "Это" not in got, f"{got}"

    @case("an English message, plain words, code, paths, links and commit ids give nothing")
    def _():
        bad = {"item", "main", "fix", "demo", "Here", "a1b2c3d4", "const", "asr", "example"}
        return not bad & set(got), f"{got}"

    @case("a system reminder is not the conversation")
    def _():
        return "<system-reminder>" not in early, ""

    @case("only what was said before the take")
    def _():
        later = terms_in(recent_text(path), GLOSSARY, common=COMMON.__contains__)
        return "LeRobot" not in got and "LeRobot" in later, f"{got} / {later}"

    @case("merge: the usual list kept, the window's new terms in place of its last ones")
    def _():
        ranked = ["Анна", "Борис", "Вера", "Глеб", "Claude", "GitHub", "Apollo", "Sonnet"]
        m = merge(["Apollo", "IMO", "LeRobot"], ranked, head=4, size=7)
        return m == ["Анна", "Борис", "Вера", "Глеб", "Claude", "IMO", "LeRobot"], f"{m}"

    @case("merge: nothing new in the window, the usual list exactly")
    def _():
        ranked = ["Анна", "Борис", "Вера", "Глеб", "Claude", "GitHub", "Apollo"]
        return merge(["apollo", "GitHub"], ranked, head=4, size=7) == ranked, ""

    @case("merge: the pinned names are never pushed out")
    def _():
        ranked = ["Анна", "Борис", "Вера", "Глеб", "Claude"]
        m = merge(["A1", "B1", "C1", "D1", "E1"], ranked, head=4, size=5)
        return m[:4] == ranked[:4] and len(m) == 5, f"{m}"

    @case("slugs, domains and file names are not terms")
    def _():
        t = terms_in("Смотри annuities-genius и claude.ai, потом annuities-genius и claude.ai, "
                     "Pages.csv и Pages.csv.", [], common=COMMON.__contains__)
        return t == [], f"{t}"

    @case("Context: the same terms through the cache, none for an unknown session")
    def _():
        ctx = Context(GLOSSARY, common=COMMON.__contains__)
        ctx._files[SID] = path
        a, b = ctx.terms(SID), ctx.terms(SID)
        return a == b and "LeRobot" in a and Context(GLOSSARY).terms("ffffffff-0000") == [], f"{a}"

    if __name__ == "__main__":
        sys.stdout.reconfigure(encoding="utf-8")
        bad = 0
        for name, fn in CASES:
            ok, info = fn()
            bad += not ok
            print(f"[{'v' if ok else 'X'}] {name}" + ("" if ok else f"   -> {info}"))
        print("all good" if not bad else f"failed: {bad}")
        sys.exit(1 if bad else 0)
