# -*- coding: utf-8 -*-
"""«Всё» where a sentence opens with «Все,» or closes with «и все» (17.10.2026).

Invented phrases only. Neither a microphone nor LM Studio is needed.

    ..\\.venv\\Scripts\\python.exe test_vsyo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_key_flow as K  # noqa: E402
from stt import __main__ as M  # noqa: E402
from stt.polish import vsyo  # noqa: E402

CASES = [
    ("opening the take", "Все, свободен.", "Всё, свободен."),
    ("opening a later sentence", "Готово. Все, поехали дальше.", "Готово. Всё, поехали дальше."),
    ("«Все, что…»", "Все, что осталось, допиши завтра.", "Всё, что осталось, допиши завтра."),
    ("after a question mark", "Понял? Все, иди.", "Понял? Всё, иди."),
    ("alone as a sentence", "Все. Работаем.", "Всё. Работаем."),
    ("the whole take", "Все", "Всё"),
    ("closing with «и все»", "Оставь как есть и все.", "Оставь как есть и всё."),
    ("«вот и все» at the end of the take", "Нажми кнопку, вот и все", "Нажми кнопку, вот и всё"),
    ("«и все» before a question mark", "Просто перезапустить и все?", "Просто перезапустить и всё?"),
    ("already «всё»: untouched", "Всё, свободен. Сделай и всё.", "Всё, свободен. Сделай и всё."),
    ("«Все, кто…»: everybody, untouched", "Все, кто пришёл, садитесь.", "Все, кто пришёл, садитесь."),
    ("«Все, которые…»: untouched", "Все, которые готовы, отправь.", "Все, которые готовы, отправь."),
    ("«все» mid-sentence: untouched", "Проверь все файлы.", "Проверь все файлы."),
    ("«Все пришли»: no comma, untouched", "Все пришли вовремя.", "Все пришли вовремя."),
    ("«и все» before a comma: untouched", "Мы и все остальные, кто там был.", "Мы и все остальные, кто там был."),
    ("«и все» inside: untouched", "Он и все его друзья ушли.", "Он и все его друзья ушли."),
    ("a longer word: untouched", "Всеволод, привет.", "Всеволод, привет."),
]


def main() -> int:
    bad = 0
    for name, text, want in CASES:
        got = vsyo(text)
        ok = got == want
        bad += not ok
        print(f"[{'v' if ok else 'X'}] {name}: {got!r}")

    # The service: a raw take the corrector did not touch is pasted with «Всё».
    pasted = []
    real_log, real_paste = M.log, M.paste_text
    M.log = lambda *_a, **_k: None
    M.paste_text = lambda text, *a, **k: pasted.append(text)
    try:
        d = K.make_processor()
        d.transcribe_resilient = lambda audio, secs=None: ("все, свободен", 0.01)
        d.process(K.speech(1.0), preroll_s=0.0)
    finally:
        M.log, M.paste_text = real_log, real_paste
    want = "Всё, свободен"
    ok = pasted == [want] and d.learner.rows[-1]["final"] == want
    bad += not ok
    print(f"[{'v' if ok else 'X'}] the service pastes and records it with «Всё»: {pasted!r}")

    print(f"\n{'all passed' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
