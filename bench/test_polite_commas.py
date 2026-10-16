# -*- coding: utf-8 -*-
"""«Пожалуйста» inside a request is set off by commas (16.10.2026).

Invented phrases only. Neither a microphone nor LM Studio is needed.

    ..\\.venv\\Scripts\\python.exe test_polite_commas.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt.polish import polite_commas  # noqa: E402

CASES = [
    ("bare on both sides", "Открой пожалуйста почту.", "Открой, пожалуйста, почту."),
    ("left comma missing", "Сделай пожалуйста, отчёт.", "Сделай, пожалуйста, отчёт."),
    ("right comma missing", "Сделай, пожалуйста отчёт.", "Сделай, пожалуйста, отчёт."),
    ("at the end of a sentence", "Убери это пожалуйста. Потом ещё.", "Убери это, пожалуйста. Потом ещё."),
    ("at the end of the take", "Дай мне пожалуйста", "Дай мне, пожалуйста"),
    ("opening a sentence", "Готово. Пожалуйста переведи его.", "Готово. Пожалуйста, переведи его."),
    ("a Latin word after", "Сделай пожалуйста handover.", "Сделай, пожалуйста, handover."),
    ("both commas there: untouched", "Открой, пожалуйста, почту.", "Открой, пожалуйста, почту."),
    ("a dash after: only the left comma", "Хочешь пожалуйста — бери.", "Хочешь, пожалуйста — бери."),
    ("after «и»: only the right one", "Проверь и пожалуйста отправь.", "Проверь и пожалуйста, отправь."),
    ("«да пожалуйста»: untouched", "Да пожалуйста.", "Да пожалуйста."),
    ("«ну пожалуйста»: untouched", "Ну пожалуйста!", "Ну пожалуйста!"),
    ("alone: untouched", "Пожалуйста.", "Пожалуйста."),
    ("in quotes: untouched", "Он сказал «пожалуйста» и ушёл.", "Он сказал «пожалуйста» и ушёл."),
    ("no such word", "Открой почту.", "Открой почту."),
]


def main() -> int:
    bad = 0
    for name, text, want in CASES:
        got = polite_commas(text)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got!r}")

    # The service: a raw take the corrector did not touch is pasted with the commas.
    pasted = []
    real_log, real_paste = M.log, M.paste_text
    M.log = lambda *_a, **_k: None
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    try:
        d = K.make_processor()
        d.transcribe_resilient = lambda audio, secs=None: ("закрой пожалуйста окно", 0.01)
        d.process(K.speech(1.0), preroll_s=0.0)
    finally:
        M.log, M.paste_text = real_log, real_paste
    want = "Закрой, пожалуйста, окно"
    ok = pasted == [want] and d.learner.rows[-1]["final"] == want
    bad += not ok
    print(f"[{'v' if ok else 'X'}] the service pastes and records it with the commas: {pasted!r}")

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
