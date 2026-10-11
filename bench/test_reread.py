# -*- coding: utf-8 -*-
"""A take whose text is short for its speech is read again (01.10.2026).

The first pass can leave speech unread: it writes "end of text" at a pause, or
skips a stretch in the middle (one nine-word sentence vanished from a 22-second
take on 25.09.2026 and the pasted text looked complete). [asr] reread_early_end
makes the app read such a take once more, with timestamps, and keep the new text
only if it is clearly fuller.

Checked here without a GPU and without loading any recognizer:
1. off (the default): one pass, nothing else;
2. a normal take is stopped by plain arithmetic, before the speech filter;
3. a short text with seconds of speech beyond it is read again, and the fuller
   text wins;
4. what must NOT win: the same text, a re-read that only brings oaths and
   fillers back, a term written as two words, the other alphabet, a stock
   subtitle line, a loop, a different reading altogether, words or a clause
   said twice;
5. long takes and takes of the short-take model are left alone, and with
   timestamps off a long take is still too long for it;
6. a failure here never loses the take — except a lost video card, which must
   reach the code that brings the model back;
7. the arithmetic itself.

    ..\\.venv\\Scripts\\python.exe test_reread.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stt import asr as A  # noqa: E402

SR = 16000
FIRST = "Сначала собери список клиентов, потом разложи его по папкам."
FULLER = ("Сначала собери список клиентов. Я не уверен, как это назвать, "
          "но потом разложи его по папкам.")


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


class FakeModel:
    """Answers the first call (no timestamps) and the re-read (timestamps) differently."""

    def __init__(self, first, again=None, boom=None):
        self.first, self.again, self.boom = first, again, boom
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if kw["without_timestamps"]:
            text = self.first
        else:
            if self.boom:
                raise self.boom
            text = self.again
        return [SimpleNamespace(text=text)], None


def secs(n):
    return np.zeros(int(SR * n), dtype=np.float32)


def chunks(*pairs):
    """(start_s, end_s) pairs -> samples."""
    return [(int(a * SR), int(b * SR)) for a, b in pairs]


# 17.5 s of speech in three stretches, for a text that needs about 4 s of it
LONG_SPEECH = chunks((0.0, 3.0), (4.0, 12.0), (13.0, 19.5))


def make(first, again=None, flag=True, vad=LONG_SPEECH, boom=None, timestamps_after_s=25.0):
    cfg = {"asr": {"model": "large-v3-turbo", "short_model": "",
                   "timestamps_after_s": timestamps_after_s,
                   "reread_early_end": flag}}
    asr = A.Asr(cfg, [])
    asr.model = FakeModel(first, again, boom)
    seen = {"vad": 0}

    def fake_vad(audio):
        seen["vad"] += 1
        if isinstance(vad, BaseException):
            raise vad
        return vad

    A.speech_chunks = fake_vad
    return asr, seen


def main():
    ok = []
    real_vad = A.speech_chunks

    # --- 1. Off by default ---
    asr, seen = make(FIRST, FULLER, flag=False)
    if A.Asr({"asr": {"model": "x"}}, []).reread_early_end:
        fail("reread_early_end must be off when the key is absent")
    text, _ = asr.transcribe(secs(20))
    if text != FIRST or len(asr.model.calls) != 1 or seen["vad"]:
        fail(f"off: one pass and no speech filter expected, got {len(asr.model.calls)} calls")
    ok.append("off by default: one pass, the speech filter is not even asked")

    # --- 2. A normal take stops at the arithmetic ---
    # 14 s of audio and a long text: the text could not be said in much less, so
    # there is no room for 6 s of unread speech and the filter is never run.
    wordy = " ".join(["Подготовь отчёт по клиентам и отправь его завтра утром"] * 3)
    asr, seen = make(wordy, FULLER)
    text, _ = asr.transcribe(secs(14))
    if text != wordy or seen["vad"] or len(asr.model.calls) != 1:
        fail(f"a normal take must stop at the arithmetic: vad={seen['vad']}, calls={len(asr.model.calls)}")
    ok.append("a normal take is stopped by arithmetic: no speech filter, no second read")

    # --- 3. A short text over a lot of speech: read again, the fuller one wins ---
    asr, seen = make(FIRST, FULLER)
    text, _ = asr.transcribe(secs(20))
    if text != FULLER:
        fail(f"the fuller re-read must replace the text, got {text!r}")
    if len(asr.model.calls) != 2 or asr.model.calls[1]["without_timestamps"]:
        fail("the second read must be with timestamps")
    if not asr.last_reread or not asr.last_reread["changed"] or asr.last_reread["uncovered_s"] < 6:
        fail(f"last_reread must tell what happened: {asr.last_reread}")
    ok.append("flagged take: read again with timestamps, the clearly fuller text wins")

    # --- 4. What must not win ---
    not_fuller = {
        "the same text": FIRST,
        "only fillers and an oath put back": "Ну, сначала собери, блин, список клиентов, потом, вот, разложи его по папкам.",
        "one term written as two words": "Сначала собери список клиентов, потом разложи его по пап кам.",
        "an English sentence in Russian letters": "Ай вил ду зис ту морроу, соу плиз гив ми э чанс ту эксплейн.",
        "a stock subtitle line": FIRST + " Редактор субтитров А. Иванов Корректор Б. Петрова",
        "a loop": "да да да " * 20,
        "a different reading altogether": "Закажи пиццу на вечер и напомни маме про выходные, пожалуйста.",
        # nothing new in it, only more of the same: counted as a set, not as occurrences
        "words said twice": "Сначала собери, собери список клиентов, клиентов, потом, потом разложи его по папкам, по папкам.",
        "a clause said twice": FIRST[:-1] + ", потом разложи его по папкам, потом разложи его по папкам.",
    }
    for why, again in not_fuller.items():
        asr, _ = make(FIRST, again)
        text, _ = asr.transcribe(secs(20))
        if text != FIRST:
            fail(f"must keep the first text: {why}: got {text!r}")
        if not (asr.last_reread and asr.last_reread["changed"] is False):
            fail(f"a refused re-read must be recorded as unchanged: {why}")
    ok.append(f"{len(not_fuller)} kinds of re-read that must not replace the text are refused")

    # --- 5. Long takes and the short-take model ---
    asr, seen = make(FIRST, FULLER)
    asr.model.first = asr.model.again = FIRST
    asr.transcribe(secs(35))
    if seen["vad"] or len(asr.model.calls) != 1:
        fail("a long take is already read with timestamps: no second look")
    asr, seen = make(FIRST, FULLER)
    asr.short = FakeModel("Сделай это.", FULLER)
    asr.short_seconds = 3.0
    asr.transcribe(secs(2), speech_s=2.0)
    if seen["vad"] or len(asr.short.calls) != 1:
        fail("a take of the short-take model must not be read again")
    ok.append("long takes and the short-take model are left alone")

    # With timestamps_after_s = 0 nothing counts as "long", so the length of the
    # take has to keep a five-minute take out of the second look (it would read
    # it in the very mode that looped on one on 22.09.2026).
    asr, seen = make(FIRST, FULLER, timestamps_after_s=0)
    text, _ = asr.transcribe(secs(40))
    if text != FIRST or seen["vad"] or len(asr.model.calls) != 1:
        fail(f"timestamps_after_s = 0 and a 40 s take: no second look expected, "
             f"vad={seen['vad']}, calls={len(asr.model.calls)}")
    asr, seen = make(FIRST, FULLER, timestamps_after_s=0)
    text, _ = asr.transcribe(secs(20))
    if text != FULLER:
        fail("timestamps_after_s = 0 must not switch the second look off for an ordinary take")
    ok.append("the second look is limited by the length of the take, also when timestamps are off")

    # --- 6. Failures ---
    asr, seen = make(FIRST, FULLER, vad=RuntimeError("onnx broke"))
    text, _ = asr.transcribe(secs(20))
    if text != FIRST:
        fail("a speech-filter failure must leave the first text")
    asr, seen = make(FIRST, boom=ValueError("odd failure"))
    text, _ = asr.transcribe(secs(20))
    if text != FIRST:
        fail("a failed re-read must leave the first text")
    asr, seen = make(FIRST, boom=RuntimeError("CUDA error: device-side assert"))
    try:
        asr.transcribe(secs(20))
        fail("a lost video card must reach the code that reloads the model")
    except RuntimeError as exc:
        if "CUDA" not in str(exc):
            raise
    ok.append("a failure keeps the take; a lost video card is passed on")

    # --- 7. The arithmetic ---
    if A.syllables("Привет, мир.") != 3 or A.syllables("Сделай 30 штук") != 3 + 3:
        fail(f"syllables: {A.syllables('Привет, мир.')}, {A.syllables('Сделай 30 штук')}")
    gap = A.uncovered_s("а" * 25, chunks((0, 10)))
    if abs(gap - 5.0) > 1e-6:
        fail(f"uncovered_s must be speech minus the least time the text needs: {gap}")
    if A.uncovered_s("а" * 60, chunks((0, 10))) > 0:
        fail("a text too long for the speech must give a negative gap")
    # the length guard on its own: a term split in three adds three "new" words
    # and loses one old one, yet says nothing more
    glued = "Открой buildreport на сайте и проверь ссылку"
    split = "Открой build the report на сайте и проверь ссылку"
    if A.clearly_fuller(glued, split):
        fail("a term written as three words instead of one is not more speech")
    if not A.clearly_fuller(glued, glued + ", потом покажи мне результат целиком"):
        fail("three new meaningful words on the end must count")
    # a stutter has no new word in it, however many occurrences it adds
    if A.clearly_fuller(FIRST, "Сначала собери, собери список клиентов, клиентов, потом, потом разложи его по папкам, по папкам."):
        fail("repeated words are not new words")
    A.speech_chunks = real_vad
    if A.speech_chunks(secs(3)) != []:
        fail("the speech filter must find nothing in silence")
    ok.append("syllable count, uncovered seconds, the speech filter on silence")

    for line in ok:
        print(f"[ok] {line}")


if __name__ == "__main__":
    main()
