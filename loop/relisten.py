# -*- coding: utf-8 -*-
"""Reads one recorded take again on the CPU, to see whether the words were
really said that way or the live read got them wrong.

    python loop/relisten.py recordings/<day>/<take>.wav
        plain read by both models (large-v3 and large-v3-turbo), no hint
    python loop/relisten.py recordings/<day>/<take>.wav --variants [model]
        the live settings, then the same with one setting off or flipped at a
        time (hint, penalty on repeated words, speech filter, timestamps), to
        find the setting that changes the text. Without [model], the model the
        app gives a take of this length (short_model up to short_seconds).

The CPU read is not the live read: the live one runs on the video card in
float16, so the two can differ by a word on their own. A word that every read
here agrees on and the live text does not is worth a closer look.
"""
import sys
import wave
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
from faster_whisper import WhisperModel  # noqa: E402
from stt import config as cfg_mod, termstats  # noqa: E402
from stt.asr import LOOP_PENALTY, Asr, build_prompt  # noqa: E402
from stt.fixes import Fixes  # noqa: E402


def load(path: str) -> np.ndarray:
    w = wave.open(path)
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def plain(audio: np.ndarray, language: str) -> None:
    for name in ("large-v3", "large-v3-turbo"):
        m = WhisperModel(name, device="cpu", compute_type="int8")
        segs, _ = m.transcribe(audio, language=language, beam_size=5)
        print(f"{name:15s}| " + " ".join(s.text.strip() for s in segs))


def variants(audio: np.ndarray, cfg: dict, name: str) -> None:
    terms = cfg_mod.glossary()
    ranked = termstats.rank(terms, cfg_mod.LOG_DIR,
                            aliases=termstats.aliases_from_fixes(Fixes(cfg_mod.FIXES_PATH)),
                            days=14, skip=cfg_mod.mywords())
    hint = build_prompt(ranked, int(cfg["asr"].get("prompt_terms", 45)),
                        cfg["asr"].get("prompt_style", "list"))
    m = WhisperModel(name, device="cpu", compute_type="int8")
    print(f"(model: {name})")
    asr = Asr(cfg, terms)
    long_take = len(audio) / 16000 > asr.timestamps_after_s
    live_rep = asr.repetition_penalty

    def run(label, prompt=hint, rep=live_rep, vad=True, ts=long_take, every=long_take):
        segs, _ = m.transcribe(
            audio, language=asr.language, beam_size=asr.beam_size,
            temperature=[0.0, 0.2, 0.4, 0.6], compression_ratio_threshold=2.4,
            repetition_penalty=rep, condition_on_previous_text=False,
            initial_prompt=None if every else prompt,
            hotwords=Asr._hotwords(m, prompt) if every and prompt else None,
            vad_filter=vad, vad_parameters={"min_silence_duration_ms": 300},
            without_timestamps=not ts)
        print(f"{label:15s}| " + " ".join(s.text.strip() for s in segs))

    run("live")
    run("no hint", prompt=None)
    run("penalty flip", rep=LOOP_PENALTY if live_rep < LOOP_PENALTY else 1.0)
    run("no vad", vad=False)
    run("timestamps flip", ts=not long_take, every=False)


def live_model(audio: np.ndarray, cfg: dict) -> str:
    """The model the app gives a take this long. Always taking the main model
    made «live» on a short take the other model's read, and a word that model
    got right looked like a chance slip of the video card."""
    a = cfg["asr"]
    main_model = a.get("model", "large-v3-turbo")
    short = a.get("short_model") or main_model
    return short if len(audio) / 16000 <= float(a.get("short_seconds", 3.0)) else main_model


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    audio = load(args[0])
    cfg = cfg_mod.load()
    if "--variants" in args:
        rest = args[args.index("--variants") + 1:]
        variants(audio, cfg, rest[0] if rest else live_model(audio, cfg))
    else:
        plain(audio, cfg["asr"].get("language", "ru"))


if __name__ == "__main__":
    main()
