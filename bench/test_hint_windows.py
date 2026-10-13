# -*- coding: utf-8 -*-
"""The hint must reach every 30-second window of a long take (01.10.2026).

With condition_on_previous_text off, faster-whisper gives the hint to the FIRST
window only and reads the others bare: no glossary, no punctuation style. Passing
the same text as `hotwords` puts it in front of every window. This is switched
by [asr] hint_every_window.

Checked here without a GPU and without loading any recognizer:
1. on (the default): a long take sends the hint as hotwords and leaves
   initial_prompt empty; off: it travels as initial_prompt, exactly as before;
2. a short take is not touched at all, either way;
3. the retries after a loop: no hint stays no hint, and the old-way fallback is
   plain again;
4. a hint longer than the decoder keeps is cut the way initial_prompt cuts it;
5. the premise itself, against the installed faster-whisper: window one gets
   the same tokens either way, windows two and up get the hint only as hotwords;
   and an over-long hint gives the same tokens as initial_prompt would keep,
   minus the few of a word the cut broke in two.
   (Needs the tokenizer file from the disk cache; skipped without it.)
6. the last, nearly empty window can read the hint back instead of the last
   words: such an end is spotted, the take is read the old way and that text
   wins, unless it ends the same way (then the words were said).

    ..\\.venv\\Scripts\\python.exe test_hint_windows.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stt.asr import LOOP_PENALTY, Asr, build_prompt  # noqa: E402

SR = 16000
CFG = {"asr": {"model": "large-v3-turbo", "short_model": "", "timestamps_after_s": 25.0,
               "prompt_style": "commands"}}
TERMS = ["Alpha Tool", "beta-runner", "Gamma", "delta sync"]


def fail(msg):
    print(f"[X] {msg}")
    sys.exit(1)


class FakeTokenizer:
    """One token per character: enough to see which end is cut."""

    class Enc:
        def __init__(self, ids):
            self.ids = ids

    def encode(self, text, add_special_tokens=True):
        return self.Enc([ord(ch) for ch in text])

    def decode(self, ids):
        return "".join(chr(i) for i in ids)


class FakeModel:
    max_length = 448
    hf_tokenizer = FakeTokenizer()

    def __init__(self, answers=("текст",)):
        self.calls = []
        self.answers = answers if callable(answers) else list(answers)

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if callable(self.answers):
            text = self.answers(kw)
        else:
            text = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(text, BaseException):
            raise text
        return [SimpleNamespace(text=text)], None


def make(flag, answers=("текст",), timestamps_after_s=25.0):
    cfg = {"asr": dict(CFG["asr"], hint_every_window=flag,
                       timestamps_after_s=timestamps_after_s)}
    asr = Asr(cfg, TERMS)
    asr.model = FakeModel(answers)
    return asr


def secs(n):
    return np.zeros(int(SR * n), dtype=np.float32)


def real_tokenizer():
    try:
        from faster_whisper.tokenizer import Tokenizer
        from faster_whisper.utils import download_model
        from tokenizers import Tokenizer as HF

        path = download_model("large-v3-turbo", local_files_only=True)
        return Tokenizer(HF.from_file(str(Path(path) / "tokenizer.json")), True,
                         task="transcribe", language="ru")
    except Exception:
        return None


def main():
    ok = []

    # --- 1. On by default (01.10.2026); off on request: nothing changes ---
    if not Asr({"asr": {"model": "x"}}, TERMS).hint_every_window:
        fail("hint_every_window must be on when the key is absent")
    asr = make(False)
    if asr.hint_every_window:
        fail("hint_every_window = false must switch it off")
    asr.transcribe(secs(40))
    kw = asr.model.calls[0]
    if kw["initial_prompt"] != asr.prompt or kw.get("hotwords"):
        fail(f"off: the hint must stay initial_prompt: {kw}")
    if len(asr.model.calls) != 1:
        fail("off: the end of the text must not be checked, there is nothing to check")
    ok.append("on by default; off on request: the hint is initial_prompt, as it was")

    # --- 2. On: long take -> hotwords; short take untouched ---
    asr = make(True)
    asr.transcribe(secs(40))
    kw = asr.model.calls[0]
    if kw["initial_prompt"] is not None or kw["hotwords"] != asr.prompt:
        fail(f"on, long: the hint must go as hotwords only: {kw}")
    if kw["without_timestamps"] or kw["condition_on_previous_text"]:
        fail("on, long: still read with timestamps and without carrying text over")
    asr = make(True)
    asr.transcribe(secs(8))
    kw = asr.model.calls[0]
    if kw["initial_prompt"] != asr.prompt or kw.get("hotwords"):
        fail(f"on, short take: must not change: {kw}")
    ok.append("on: a long take sends hotwords, a short take is untouched")

    # --- 3. Retries ---
    # With no penalty on repeated words (the default), a loop is first read
    # again with the penalty and the same hint; only a second loop drops the
    # hint, and a third goes back to the old way.
    loop = "да " * 40
    asr = make(True, answers=[loop, "Нормальный текст."])
    text, _ = asr.transcribe(secs(40))
    first, second = asr.model.calls
    if text != "Нормальный текст.":
        fail(f"loop retry lost the text: {text!r}")
    if second["repetition_penalty"] != LOOP_PENALTY or second["hotwords"] != asr.prompt:
        fail(f"the first retry after a loop must add the penalty and keep the hint: {second}")
    asr = make(True, answers=[loop, loop, "Нормальный текст."])
    text, _ = asr.transcribe(secs(40))
    third = asr.model.calls[2]
    if text != "Нормальный текст.":
        fail(f"the retry without the hint lost the text: {text!r}")
    if third["initial_prompt"] is not None or third.get("hotwords"):
        fail(f"a second loop must be read with no hint at all: {third}")
    asr = make(True, answers=[loop, loop, loop, "Спокойный текст."])
    text, _ = asr.transcribe(secs(300))
    fourth = asr.model.calls[3]
    if text != "Спокойный текст." or not fourth["without_timestamps"]:
        fail("the old-way fallback after three loops went wrong")
    if fourth["initial_prompt"] != asr.prompt or fourth.get("hotwords"):
        fail(f"the old-way fallback must use the plain prompt: {fourth}")
    ok.append("a loop: penalty first, then no hint, then the old way with the plain prompt")

    # --- 4. A hint longer than the decoder keeps ---
    asr = make(True)
    limit = 448 // 2 - 1
    short_hint = "а" * limit
    if Asr._hotwords(asr.model, short_hint) != short_hint:
        fail("a hint that fits must pass through unchanged")
    long_hint = "".join(chr(0x430 + i % 30) for i in range(limit + 40))
    cut = Asr._hotwords(asr.model, " " + long_hint)
    if cut != long_hint[-limit:].strip():
        fail("an over-long hint must keep its LAST tokens, like initial_prompt does")
    if Asr._hotwords(SimpleNamespace(), long_hint) != long_hint:
        fail("a model without a tokenizer must leave the hint as it is")
    # with words: the cut starts at the first word that opens inside the kept tail
    words = " ".join("слово" + chr(0x430 + i % 30) * 3 for i in range(60))
    cut = Asr._hotwords(asr.model, words)
    if not words.endswith(cut) or cut.startswith(" ") or not words[-len(cut) - 1] == " ":
        fail(f"an over-long hint must be cut at the start of a word: {cut[:20]!r}")
    if len(" " + cut) > limit or len(" " + cut) < limit - 12:
        fail(f"the cut hint must fill the limit but not pass it: {len(' ' + cut)} of {limit}")
    ok.append(f"a hint over {limit} tokens is cut from the front, at a word, like initial_prompt")

    # --- 5. The premise, against the installed faster-whisper ---
    tok = real_tokenizer()
    if tok is None:
        print("[skip] real tokenizer is not in the disk cache: premise check skipped")
    else:
        from faster_whisper.transcribe import WhisperModel

        fake_self = SimpleNamespace(max_length=448)
        prompt = Asr(CFG, TERMS).prompt
        ids = tok.encode(" " + prompt.strip())
        if len(ids) > 223:
            fail(f"the test hint is {len(ids)} tokens, too long for this check")

        def get(previous, hotwords=None):
            return WhisperModel.get_prompt(fake_self, tok, previous, True, None, hotwords)

        # window one: the hint as the first window's previous text, or as hotwords
        if get(ids) != get([], prompt):
            fail("window one is not token-identical between the two ways")
        # windows two and up: the first window's text is dropped (condition off),
        # so only hotwords still carry the hint
        bare, with_hw = get([]), get([], prompt)
        if len(bare) >= len(with_hw) or bare != with_hw[-len(bare):]:
            fail("hotwords must put the hint in front of a window that has none")
        ok.append(f"real tokenizer: window one identical ({len(ids)} tokens), "
                  "later windows get the hint only as hotwords")

        # An over-long hint (more terms, or longer ones): initial_prompt keeps the
        # last 223 tokens, the cut usually inside a word. hotwords cannot start
        # inside a word (a space is put in front and the text encoded again), so
        # it must start at the next word: the same tokens as initial_prompt keeps,
        # minus those of the broken one, and the closing full stop still there.
        # Measured 01.10.2026 on 200 random hints of 241-296 tokens: the plain
        # decode of the tail gave the same tokens as initial_prompt in 50 and
        # lost the closing full stop in 57; cut at a word, the tail is always
        # initial_prompt's own, minus 0-4 tokens of the broken first word.
        import random

        rnd = random.Random(20261001)
        syl = ["ра", "ко", "ли", "ме", "та", "не", "фо", "ти", "су", "ва", "ден", "кор"]
        model = SimpleNamespace(max_length=448, hf_tokenizer=tok.tokenizer)
        checked = same = 0
        for _ in range(60):
            extra = ["".join(rnd.choice(syl) for _ in range(rnd.randint(2, 4))).capitalize()
                     for _ in range(rnd.randint(30, 45))]
            hint = build_prompt(TERMS + extra, 200, "commands")
            ids = tok.encode(" " + hint.strip())
            if len(ids) <= 223:
                continue
            checked += 1
            ip = get(ids)
            hw = get([], Asr._hotwords(model, hint))
            tail = len(tok.sot_sequence) + 1      # the sot sequence and no_timestamps close the prompt
            ip_body, hw_body = ip[1:-tail], hw[1:-tail]
            dropped = len(ip_body) - len(hw_body)
            if not (0 <= dropped <= 8 and ip_body[dropped:] == hw_body):
                fail(f"an over-long hint ({len(ids)} tokens): hotwords is not the tail of what "
                     f"initial_prompt keeps (dropped {dropped})")
            if hw_body[-1] != ip_body[-1]:
                fail("an over-long hint lost its closing token")
            same += dropped == 0
        if checked < 20:
            fail(f"the check of over-long hints saw only {checked} of them")
        ok.append(f"real tokenizer: {checked} over-long hints keep exactly the tail initial_prompt "
                  f"keeps ({same} unchanged, the rest minus a broken first word)")

    # --- 6. The hint read back in the last window ---
    from stt.asr import HINT_MARKS, hint_echo_at, hint_terms

    base = Asr(CFG, TERMS).prompt
    HEADING = HINT_MARKS[0].rstrip(":")      # the hint's own words, not speech
    if hint_terms(base) != TERMS:
        fail(f"the terms must be read back out of the hint: {hint_terms(base)}")
    if hint_terms("без списка") != []:
        fail("a hint with no list has no terms")
    spot = {
        "two terms from the hint, then the final mark": "Сделай отчёт по клиентам, Alpha Tool, beta-runner.",
        "two terms glued to the last word, no comma before": "Проверь как там дела у них Alpha Tool, beta-runner.",
        "a longer run, and a made-up item after it": "Сделай отчёт, Alpha Tool, beta-runner, Gamma, Омега.",
        "case does not matter": "Сделай отчёт, alpha tool, BETA-RUNNER.",
        "a run from the middle of the list": "Сделай отчёт, beta-runner, Gamma, delta sync.",
        "no mark at all": "Сделай отчёт, Alpha Tool, beta-runner",
        "the heading of the list": "Сделай отчёт, " + HEADING.lower() + ".",
        "the heading, over and over": "Сделай отчёт. " + f"{HEADING}. " * 2,
        "the heading with its colon": "Сделай отчёт, " + HEADING + ":",
    }
    for why, text in spot.items():
        if hint_echo_at(text, base) < 0:
            fail(f"an echo must be spotted: {why}: {text!r}")
    quiet = {
        "one term": "Сделай отчёт, потом запусти Alpha Tool.",
        "two terms in the wrong order": "Сделай отчёт, beta-runner, Alpha Tool.",
        "two terms that are not neighbours in the hint": "Сделай отчёт, Alpha Tool, Gamma.",
        "terms with words between them": "Alpha Tool и beta-runner нам нужны.",
        "the run is not at the end": "Alpha Tool, beta-runner, надо описать это и отправить сегодня.",
        "three made-up items after it": "Сделай отчёт, Alpha Tool, beta-runner, Омега, Сигма, Дельта.",
        "part of a longer word": "Сделай отчёт, Alpha Tools, beta-runner.",
        "a plain take": "Сделай отчёт по клиентам и отправь его завтра утром.",
        "no text": "",
        "the heading not at the end": "Термины и названия надо проверить сегодня.",
        "only half the heading": "Добавь в отчёт термины.",
        "the heading inside a longer word": "Сделай отчёт, сверхтермины и названия.",
    }
    for why, text in quiet.items():
        if hint_echo_at(text, base) >= 0:
            fail(f"not an echo: {why}: {text!r}")
    ok.append(f"a hint read back is told from speech: {len(spot)} shapes spotted, {len(quiet)} left alone")

    def window_two(echo, old_way):
        """A model that writes `echo` when the hint is hotwords, `old_way` when it is not."""
        return lambda kw: echo if kw.get("hotwords") else old_way

    REAL = "Сделай отчёт по клиентам и отправь его завтра утром"
    ECHO = REAL + ", Alpha Tool, beta-runner."
    OLD = REAL + ", потом позвони."
    # the end is the hint, the old way has the real last words: the old way wins
    asr = make(True, answers=window_two(ECHO, OLD))
    text, _ = asr.transcribe(secs(40))
    first, second = asr.model.calls
    if text != OLD:
        fail(f"the old reading must replace an echo: {text!r}")
    if not first["hotwords"] or second["hotwords"] or second["initial_prompt"] != asr.prompt \
            or second["without_timestamps"]:
        fail(f"the second read must be the old way with timestamps: {second}")
    if asr.last_echo != {"used": "old way", "why": "echo"}:
        fail(f"last_echo must tell what happened: {asr.last_echo}")
    # the same, with a credits line stuck behind the echo (seen once)
    asr = make(True, answers=window_two(
        REAL + ", Alpha Tool, beta-runner, Gamma, Омега, Редактор субтитров А.Семкин Корректор А.Егорова", OLD))
    text, _ = asr.transcribe(secs(40))
    if text != OLD or len(asr.model.calls) != 2:
        fail(f"an echo with a credits line behind it must give the old reading: {text!r}")
    # the same words are in the old reading too: they were said, the first reading stays
    asr = make(True, answers=window_two(ECHO, ECHO))
    text, _ = asr.transcribe(secs(40))
    if text != ECHO or len(asr.model.calls) != 2 or asr.last_echo["used"] != "first":
        fail(f"words that were really said must stay: {text!r}, {asr.last_echo}")
    # the heading of the list read back: the old way has the real last words
    asr = make(True, answers=window_two(REAL + ", " + f"{HEADING}. " * 2, OLD))
    text, _ = asr.transcribe(secs(40))
    if text != OLD or len(asr.model.calls) != 2:
        fail(f"the heading read back must give the old reading: {text!r}")
    # an ordinary take is read once, and so is a take that ends in a single term
    for plain in (REAL + ".", REAL + ", потом запусти Alpha Tool."):
        asr = make(True, answers=[plain])
        text, _ = asr.transcribe(secs(40))
        if text != plain or len(asr.model.calls) != 1 or asr.last_echo is not None:
            fail(f"nothing to check, one read expected: {text!r}, {len(asr.model.calls)}")
    # a short take has no windows after the first: not checked, whatever it ends with
    asr = make(True, answers=[ECHO])
    text, _ = asr.transcribe(secs(8))
    if text != ECHO or len(asr.model.calls) != 1:
        fail("a short take is never checked for an echo")
    # the old reading cannot be trusted: a loop, nothing, a failure
    for why, old_way in (("a loop", "да " * 40), ("nothing", "  "), ("a failure", ValueError("odd"))):
        asr = make(True, answers=window_two(ECHO, old_way))
        text, _ = asr.transcribe(secs(40))
        if text != ECHO or len(asr.model.calls) != 2:
            fail(f"the first reading must stay when the old way gives {why}: {text!r}")
    asr = make(True, answers=window_two(ECHO, RuntimeError("CUDA error: device-side assert")))
    try:
        asr.transcribe(secs(40))
        fail("a lost video card must reach the code that reloads the model")
    except RuntimeError as exc:
        if "CUDA" not in str(exc):
            raise
    # the terms are read from the CURRENT hint: the app rebuilds it when a word is added
    asr = make(True, answers=window_two(REAL + ", Omega Suite, Sigma-run.", OLD))
    text, _ = asr.transcribe(secs(40))
    if text != REAL + ", Omega Suite, Sigma-run." or len(asr.model.calls) != 1:
        fail("terms that are not in the hint are not an echo")
    asr.prompt = Asr(CFG, ["Omega Suite", "Sigma-run", "Tau"]).prompt
    asr.model.calls.clear()
    text, _ = asr.transcribe(secs(40))
    if text != OLD or len(asr.model.calls) != 2:
        fail(f"the check must follow a rebuilt hint: {text!r}")
    ok.append("the hint read back in the last window: the old reading replaces it, unless it says the same")

    for line in ok:
        print(f"[ok] {line}")


if __name__ == "__main__":
    main()
