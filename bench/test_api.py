# -*- coding: utf-8 -*-
"""Проверка страницы диктовок: все действия, которые делает браузер.

Диктовка должна быть запущена.
    ..\\.venv\\Scripts\\python.exe test_api.py
"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402

BASE = f"http://127.0.0.1:{cfg_mod.load().get('web', {}).get('port', 8756)}"

checks: list[tuple[bool, str, str]] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    checks.append((ok, what, detail))
    return ok


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=10.0)

    # --- страница ---
    r = c.get("/")
    check(r.status_code == 200 and "<title>" in r.text,
          "страница открывается", f"{r.status_code}, {len(r.text)} байт")

    # --- список ---
    recs = c.get("/api/records", params={"limit": 50}).json()
    if not check(bool(recs), "список диктовок не пустой", f"{len(recs)} шт"):
        return report()
    rec = recs[0]
    check(all(k in rec for k in ("id", "time", "text", "seconds", "bad")),
          "у записи есть все поля", ", ".join(sorted(rec)[:6]) + "…")

    rid = rec["id"]
    original = rec["text"]

    # --- звук ---
    a = c.get("/api/audio", params={"id": rid})
    check(a.status_code == 200 and a.content[:4] == b"RIFF",
          "звук отдаётся и это правда wav", f"{a.status_code}, {len(a.content)//1024} КБ")

    # --- пометка «плохо» ---
    def bad_count() -> int:
        return len(c.get("/api/records", params={"bad": "1"}).json())

    before = bad_count()
    c.post("/api/mark", json={"id": rid, "bad": True})
    after_on = bad_count()
    c.post("/api/mark", json={"id": rid, "bad": False})
    after_off = bad_count()
    check(after_on == before + 1 and after_off == before,
          "пометка «плохо» ставится и снимается",
          f"было {before}, с пометкой {after_on}, снова {after_off}")

    # --- поиск ---
    word = (original.split() or ["а"])[0].strip(".,!?")
    found = c.get("/api/records", params={"q": word}).json()
    check(any(x["id"] == rid for x in found),
          f"поиск находит по слову «{word}»", f"{len(found)} совпадений")

    # --- правка текста ---
    probe = original + " ПРОВЕРКА"
    res = c.post("/api/text", json={"id": rid, "text": probe}).json()
    again = c.get("/api/records", params={"limit": 5}).json()
    saved = next((x for x in again if x["id"] == rid), {})
    check(saved.get("text") == probe, "правка текста сохраняется")
    check(saved.get("corrected") == probe, "правка помечена как твоя")
    # рядом со звуком должен лежать тот же текст — это и есть материал обучения
    wav = None
    for day in sorted(cfg_mod.REC_DIR.glob("*")):
        for f in day.glob("*.txt"):
            if f.read_text(encoding="utf-8").strip() == probe.strip():
                wav = f
    check(wav is not None, "текст лёг рядом со звуком для дообучения",
          str(wav.name) if wav else "не найден")
    # возвращаем как было
    c.post("/api/text", json={"id": rid, "text": original})
    back = c.get("/api/records", params={"limit": 5}).json()
    check(next((x for x in back if x["id"] == rid), {}).get("text") == original,
          "правку можно откатить")

    # --- словари ---
    files = c.get("/api/files").json()
    check(set(files) == {"fixes", "glossary", "mywords"} and files["fixes"],
          "словари читаются", ", ".join(f"{k}: {len(v)} симв." for k, v in files.items()))

    fixes_before = files["fixes"]
    c.post("/api/files", json={"which": "fixes", "text": fixes_before + "\nпроверкаслово\tПРОВЕРКА\t0\ttest"})
    after = c.get("/api/files").json()["fixes"]
    check("проверкаслово" in after, "словарь записывается")
    c.post("/api/files", json={"which": "fixes", "text": fixes_before})
    check("проверкаслово" not in c.get("/api/files").json()["fixes"],
          "словарь возвращается как был")

    bad_target = c.post("/api/files", json={"which": "хакер", "text": "x"})
    check(bad_target.status_code == 400, "чужой файл записать нельзя",
          f"HTTP {bad_target.status_code}")

    # --- статистика ---
    st = c.get("/api/stats").json()
    check(st["records"] > 0 and "train_percent" in st, "статистика считается",
          f"{st['records']} записей, {st['minutes']} мин, обучение {st['train_percent']}%")

    report()


def report() -> None:
    bad = [c for c in checks if not c[0]]
    for ok, what, detail in checks:
        print(f"[{'v' if ok else 'X'}] {what}" + (f"  — {detail}" if detail else ""))
    print(f"\n{len(checks)-len(bad)} из {len(checks)} сошлось")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
