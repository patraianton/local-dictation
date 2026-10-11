# -*- coding: utf-8 -*-
"""Long takes lost words at the 30-second window boundary (30.09.2026).

The recognizer reads sound in 30-second windows. Without timestamps it cannot
say where it stopped, so whatever it had not finished in one window was skipped
and the next one started after it. Over September, 78 of 136 takes with more
than 30 s of speech lost words that way, and a few got a stock subtitle line
("Продолжение следует...") pasted onto the end.

Checked here without loading any model:
1. long takes are read with timestamps, short ones exactly as before;
2. a loop in timestamp mode falls back to the old way;
3. a stock line on the END of a long take is cut, real speech is not;
4. a loop read with no penalty on repeated words is read again with it first
   (08.10.2026, see LOOP_PENALTY in asr.py).

    ..\\.venv\\Scripts\\python.exe test_long_takes.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stt.asr import LOOP_PENALTY, Asr, strip_ghost_tail  # noqa: E402

# hint_every_window is off here: this test is about timestamps and about the
# tail line; how the hint travels is test_hint_windows.py's business.
CFG = {"asr": {"model": "large-v3-turbo", "short_model": "", "timestamps_after_s": 25.0,
               "hint_every_window": False}}


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def fake(answers):
    """An Asr whose _run records how it was called and answers from a list."""
    asr = Asr(CFG, ["Claude Code"])
    asr.model = object()
    calls = []

    def run(audio, prompt, model=None, timestamps=False, every_window=False, penalty=None):
        calls.append({"prompt": prompt, "timestamps": timestamps})
        if penalty is not None:
            calls[-1]["penalty"] = penalty
        return answers[min(len(calls) - 1, len(answers) - 1)]

    asr._run = run
    return asr, calls


def secs(n):
    return np.zeros(int(16000 * n), dtype=np.float32)


def main():
    ok = []

    # --- 1. The switch is by length ---
    asr, calls = fake(["Сделай отчёт."])
    asr.transcribe(secs(8))
    if calls != [{"prompt": asr.prompt, "timestamps": False}]:
        fail(f"a short take must be read the old way: {calls}")
    asr, calls = fake(["Длинная диктовка."])
    asr.transcribe(secs(34))
    if calls != [{"prompt": asr.prompt, "timestamps": True}]:
        fail(f"a long take must be read with timestamps: {calls}")
    ok.append("8 s — the old way, 34 s — with timestamps")

    asr, calls = fake(["x"])
    asr.timestamps_after_s = 0
    asr.transcribe(secs(60))
    if calls[0]["timestamps"]:
        fail("timestamps_after_s = 0 must switch the new way off")
    ok.append("timestamps_after_s = 0 turns it off")

    # --- 2. A loop with timestamps ends in the old way ---
    # (with the penalty already on, as before 08.10.2026: no extra reading)
    loop = "да " * 40
    asr, calls = fake([loop, loop, "Нормальный текст."])
    asr.repetition_penalty = LOOP_PENALTY
    text, _ = asr.transcribe(secs(300))
    if text != "Нормальный текст." or [c["timestamps"] for c in calls] != [True, True, False]:
        fail(f"loop fallback went wrong: {text!r}, {calls}")
    ok.append("a loop in both timestamp runs falls back to the old way")

    # --- 4. With no penalty, a loop is read again with it before anything else ---
    asr, calls = fake([loop, "Нормальный текст."])
    text, _ = asr.transcribe(secs(8))
    if (text != "Нормальный текст." or len(calls) != 2
            or calls[1].get("penalty") != LOOP_PENALTY or calls[1]["prompt"] != asr.prompt):
        fail(f"no-penalty loop not retried with the penalty: {text!r}, {calls}")
    asr, calls = fake([loop, loop, "Нормальный текст."])
    text, _ = asr.transcribe(secs(8))
    if text != "Нормальный текст." or calls[2]["prompt"] is not None:
        fail(f"a loop that survives the penalty must go on to the reading without the hint: {calls}")
    asr, calls = fake(["Сделай отчёт."])
    asr.transcribe(secs(8))
    if len(calls) != 1 or "penalty" in calls[0]:
        fail(f"a normal take must be read once, with the configured penalty: {calls}")
    ok.append("no-penalty loop: read again with the penalty, then the old fallbacks")

    # --- 3. Stock lines on the end ---
    cases = [
        ("Потом проверь, что ссылки открываются Субтитры сделал DimaTorzok",
         "Потом проверь, что ссылки открываются."),
        ("Сначала собери список. Потом разложи по папкам. Продолжение следует...",
         "Сначала собери список. Потом разложи по папкам."),
        ("добавь раздел про тарифы, и так далее — субтитры создавал DimaTorzok.",
         "добавь раздел про тарифы, и так далее."),
        ("Всё. Продолжение следует... Продолжение следует... Субтитры создавал DimaTorzok",
         "Всё."),
        # the credits line with initials: the full stop after an initial is not
        # the end of the line (10.09.2026: pasted whole, because the match died there)
        ("Обычный текст про дела. Редактор субтитров А.Семкин Корректор А.Егорова",
         "Обычный текст про дела."),
        ("Обычный текст про дела. Редактор субтитров А. Семкин Корректор А. Егорова",
         "Обычный текст про дела."),
        ("Потом проверь ссылки, Редактор субтитров Я.Новиков",
         "Потом проверь ссылки."),
    ]
    for src, want in cases:
        got = strip_ghost_tail(src)
        if got != want:
            fail(f"tail not cut: {src!r} -> {got!r}, expected {want!r}")
    keep = [
        "Продолжение следует, я потом допишу этот кусок про агентов.",
        "сделай субтитры к этому ролику и залей на канал",
        "В конце ролика скажи: спасибо за просмотр и подписывайтесь на канал.",
        "Продолжение следует...",   # a whole-take ghost is subtitle_ghost's job
        # initials in real speech are not a credits line
        "Потом проверь, что А.Иванов и Б.Петров получили доступ к таблице.",
    ]
    for src in keep:
        if strip_ghost_tail(src) != src:
            fail(f"real speech was cut: {src!r} -> {strip_ghost_tail(src)!r}")
    ok.append(f"{len(cases)} stuck-on lines cut (initials included), {len(keep)} real phrases left alone")

    asr, calls = fake(["Проверь доску. Продолжение следует..."])
    text, _ = asr.transcribe(secs(5))
    if text != "Проверь доску. Продолжение следует...":
        fail("the tail cut must only apply to long takes")
    ok.append("short takes are never cut")

    for line in ok:
        print(f"[ok] {line}")


if __name__ == "__main__":
    main()
