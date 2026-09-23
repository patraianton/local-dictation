# -*- coding: utf-8 -*-
"""Короткие фразы идут ко второй, тяжёлой модели.

10.09.2026 Антон сказал: «ничего нормально не транскрибирует». Замер показал,
что распознавание в целом такое же, как всю неделю, но рвутся именно короткие
приказы в одну-три секунды, и в тот день их было особенно много. На 258
коротких надиктовках large-v3 обошла turbo по слепой оценке 32:14, а на
длинных проиграла и вчетверо медленнее. Отсюда — переключение по длине.

Настоящих моделей и видеокарты здесь не надо: обе подделаны.

    ..\\.venv\\Scripts\\python.exe test_short_model.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import asr as A  # noqa: E402

SR = 16000


class FakeModel:
    """Подделка модели: отвечает своим именем и считает обращения."""

    def __init__(self, name, text=None):
        self.name = name
        self.calls = 0
        self.text = text or f"ответ от {name}"

    def transcribe(self, audio, **kw):
        self.calls += 1
        self.last_kw = kw

        class Seg:
            def __init__(s, t): s.text = t
        return [Seg(self.text)], None


def make(cfg_extra=None, both=True, text_short=None, text_main=None):
    cfg = {"asr": {"model": "large-v3-turbo", "language": "ru", "beam_size": 5,
                   "vad": True, "device": "cuda", "compute_type": "float16",
                   "short_model": "large-v3", "short_seconds": 3.0}}
    cfg["asr"].update(cfg_extra or {})
    a = A.Asr(cfg, [])
    a.model = FakeModel("large-v3-turbo", text_main)
    a.short = FakeModel("large-v3", text_short) if both else None
    return a


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


def main():
    ok = []

    # --- 1. По длине выбирается нужная модель ---
    a = make()
    for secs, want in ((0.5, "large-v3"), (2.9, "large-v3"), (3.0, "large-v3"),
                       (3.1, "large-v3-turbo"), (30.0, "large-v3-turbo")):
        _model, name = a.pick(np.zeros(int(SR * secs), dtype=np.float32))
        if name != want:
            fail(f"{secs} с ушло в {name}, а надо в {want}")
    ok.append("до трёх секунд — тяжёлая модель, дальше — быстрая")

    # --- 1a. Считается время РЕЧИ, а не длина файла: в файле ещё полсекунды
    #         предзаписи и хвост, из-за них фраза на 2,5 с уезжала к быстрой ---
    long_file = np.zeros(int(SR * 3.5), dtype=np.float32)   # файл длиннее порога
    if a.pick(long_file, speech_s=2.5)[1] != "large-v3":
        fail("речь 2,5 с ушла к быстрой модели из-за предзаписи в файле")
    if a.pick(np.zeros(SR, dtype=np.float32), speech_s=9.0)[1] != "large-v3-turbo":
        fail("длинная речь ушла к тяжёлой модели")
    t, _ = a.transcribe(long_file, speech_s=2.5)
    if a.last_model != "large-v3":
        fail("transcribe не передал время речи в выбор модели")
    ok.append("решает время речи, а не длина файла с предзаписью")

    # --- 2. Текст берётся именно у выбранной модели, и это видно в журнале ---
    a = make(text_short="короткая модель", text_main="быстрая модель")
    t, _ = a.transcribe(np.zeros(SR, dtype=np.float32))
    if t != "короткая модель" or a.last_model != "large-v3":
        fail(f"короткую надиктовку сделала не та модель: {t!r} / {a.last_model}")
    if a.model.calls:
        fail("быструю модель зря побеспокоили")
    t, _ = a.transcribe(np.zeros(SR * 10, dtype=np.float32))
    if t != "быстрая модель" or a.last_model != "large-v3-turbo":
        fail(f"длинную надиктовку сделала не та модель: {t!r} / {a.last_model}")
    ok.append("в журнал пишется, какая модель сделала надиктовку")

    # --- 3. Второй модели нет — всё идёт по-старому, ничего не падает ---
    a = make(both=False, text_main="быстрая модель")
    t, _ = a.transcribe(np.zeros(SR, dtype=np.float32))
    if t != "быстрая модель" or a.last_model != "large-v3-turbo":
        fail("без второй модели короткая надиктовка не прошла по-старому")
    ok.append("без второй модели работает как раньше")

    # --- 4. Выключение настройкой ---
    a = A.Asr({"asr": {"model": "large-v3-turbo", "short_model": ""}}, [])
    if a.short_model_name:
        fail("пустая настройка не выключила вторую модель")
    a = A.Asr({"asr": {"model": "large-v3-turbo", "short_model": "large-v3-turbo"}}, [])
    a.model = FakeModel("large-v3-turbo")
    # та же модель второй раз в память не грузится — это проверяется в load(),
    # здесь достаточно, что имена совпали и pick() отдаёт основную
    if a.pick(np.zeros(SR, dtype=np.float32))[1] != "large-v3-turbo":
        fail("та же модель, а pick отдал что-то другое")
    ok.append("выключается настройкой; та же модель дважды не грузится")

    # --- 5. Зацикливание лечится тем же способом на любой модели ---
    class Looping(FakeModel):
        def transcribe(self, audio, **kw):
            self.calls += 1
            self.last_kw = kw

            class Seg:
                def __init__(s, t): s.text = t
            # первый раз — каша с повтором, второй (без подсказки) — нормально
            if kw.get("initial_prompt"):
                return [Seg("да да да да да да да да да да да да")], None
            return [Seg("да, хорошо")], None

    a = make()
    a.short = Looping("large-v3")
    a.prompt = "подсказка"
    t, _ = a.transcribe(np.zeros(SR, dtype=np.float32))
    if t != "да, хорошо":
        fail(f"зацикливание на короткой модели не вылечено: {t!r}")
    if a.short.calls != 2:
        fail("повтор без подсказки не сделан")
    ok.append("зацикливание лечится и на второй модели")

    # --- 6. Прогрев трогает обе модели ---
    a = make()
    a.question_score = lambda *args, **kw: None
    a.warmup()
    if not a.short.calls or not a.model.calls:
        fail(f"прогрели не обе модели: короткая {a.short.calls}, быстрая {a.model.calls}")
    ok.append("на старте прогреваются обе модели")

    # --- 7. После потери видеокарты вторая модель не остаётся мёртвой ---
    a = make()
    dead = a.short
    try:
        a.reload()
    except Exception:
        pass
    if a.short is dead:
        fail("после перезагрузки осталась старая, мёртвая вторая модель")
    ok.append("после потери видеокарты вторая модель не остаётся мёртвой")

    print("[v] " + "\n[v] ".join(ok))
    print("\nвсё сошлось")


if __name__ == "__main__":
    main()
