# -*- coding: utf-8 -*-
"""A take is pasted starting with a capital letter (10.10.2026).

Invented phrases only. Neither a microphone nor LM Studio is needed.

    ..\\.venv\\Scripts\\python.exe test_open_capital.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt.polish import open_with_capital  # noqa: E402

CASES = [
    ("a small Russian letter opens the take", "проверь почту", "Проверь почту"),
    ("ё too", "ёлку поставь", "Ёлку поставь"),
    ("leading spaces kept", "  и ещё одно", "  И ещё одно"),
    ("a capital already: untouched", "Проверь почту.", "Проверь почту."),
    ("a Latin term stays as written", "herdr не видит окно", "herdr не видит окно"),
    ("an ellipsis first: untouched", "...и тогда", "...и тогда"),
    ("only the first letter", "пиши. потом ещё", "Пиши. потом ещё"),
    ("empty", "", ""),
]


def main() -> int:
    bad = 0
    for name, text, want in CASES:
        got = open_with_capital(text)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got!r}")

    # The service: a raw take that nobody gave a capital is pasted with one.
    pasted = []
    real_log, real_paste = M.log, M.paste_text
    M.log = lambda *_a, **_k: None
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    try:
        d = K.make_processor()
        d.transcribe_resilient = lambda audio, secs=None: ("закрой окно", 0.01)
        d.process(K.speech(1.0), preroll_s=0.0)
    finally:
        M.log, M.paste_text = real_log, real_paste
    ok = pasted == ["Закрой окно"] and d.learner.rows[-1]["final"] == "Закрой окно"
    bad += not ok
    print(f"[{'v' if ok else 'X'}] the service pastes and records it with a capital: {pasted!r}")

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
