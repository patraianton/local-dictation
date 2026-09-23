# -*- coding: utf-8 -*-
"""Why did the recognizer write THAT? Re-runs one take and weighs alternatives.

    python bench/why.py <record-id> "<what was actually said>" ["<another variant>" ...]
    python bench/why.py <record-id> --with "наматрасник,другое слово" "<candidate>"

--with puts those words at the front of the hint: "would adding them to the
glossary fix this take?" without touching glossary.txt.

For the take it prints:
  * what the app's exact settings produce (hint, beam, VAD — all as in config.toml);
  * the same without the hint, and with beam 1;
  * how sure the model is of every word in its own answer;
  * for every candidate text (its own answer first, then yours): how likely the
    model finds it under teacher forcing — the model listens to the audio and is
    forced to write exactly that text; the per-token probabilities say how much
    it "agrees". Higher (closer to 0) log-probability = the model likes it more.

Loads its own copy of the Whisper model (about 1.6 GB of VRAM for turbo). It
does not touch LM Studio.
"""
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cuda_fix  # noqa: E402

cuda_fix.enable()

from faster_whisper.tokenizer import Tokenizer  # noqa: E402

from stt import audio as audio_mod  # noqa: E402
from stt import config as cfg_mod  # noqa: E402
from stt import termstats  # noqa: E402
from stt.asr import Asr, build_prompt  # noqa: E402
from stt.fixes import Fixes  # noqa: E402


def find_record(rec_id: str) -> dict:
    for lf in sorted(cfg_mod.LOG_DIR.glob("*.jsonl")):
        for line in lf.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("id") == rec_id:
                return r
    raise SystemExit(f"record {rec_id} not found in {cfg_mod.LOG_DIR}")


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == 16000 and wf.getnchannels() == 1
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def app_hint(cfg: dict, extra: list[str] | None = None) -> str:
    """The exact hint the running app builds (same ranking, same style)."""
    terms = cfg_mod.glossary()
    fixes = Fixes(cfg_mod.FIXES_PATH)
    a = cfg.get("asr", {})
    try:
        ranked = termstats.rank(
            terms, cfg_mod.LOG_DIR,
            aliases=termstats.aliases_from_fixes(fixes),
            days=int(a.get("prompt_days", 14)),
            skip=cfg_mod.mywords(),
        )
    except Exception:
        ranked = list(terms)
    if extra:
        ranked = [t for t in extra if t] + [t for t in ranked if t not in extra]
    return build_prompt(ranked, int(a.get("prompt_terms", 45)), a.get("prompt_style", "sample"))


def word_confidence(asr: Asr, audio: np.ndarray, prompt: str | None) -> list[tuple[str, float]]:
    segs, _ = asr.model.transcribe(
        audio, language=asr.language, beam_size=asr.beam_size,
        temperature=[0.0, 0.2, 0.4, 0.6], compression_ratio_threshold=2.4,
        repetition_penalty=1.15, condition_on_previous_text=False,
        initial_prompt=prompt or None, vad_filter=asr.vad,
        vad_parameters={"min_silence_duration_ms": 300}, word_timestamps=True,
    )
    out = []
    for s in segs:
        for w in s.words or []:
            out.append((w.word, w.probability))
    return out


def forced_scores(asr: Asr, tok: Tokenizer, audio: np.ndarray, text: str,
                  prompt: str | None) -> tuple[float, list[tuple[str, float]]]:
    """Teacher forcing: total log-prob of `text` and per-word mean probability."""
    model = asr.model
    features = model.feature_extractor(audio)
    n_frames = features.shape[-1]
    from faster_whisper.audio import pad_or_trim

    segment = pad_or_trim(features)
    enc = model.encode(segment)
    start = list(tok.sot_sequence)
    if prompt:
        start = [tok.sot_prev] + tok.encode(" " + prompt.strip())[-(model.max_length // 2 - 1):] + start
    text_tokens = tok.encode(" " + text.strip())
    res = model.model.align(enc, start, [text_tokens], n_frames, median_filter_width=7)[0]
    probs = list(res.text_token_probs)
    total = sum(math.log(max(p, 1e-9)) for p in probs)
    words, word_tokens = tok.split_to_word_tokens(text_tokens + [tok.eot])
    bounds = np.pad(np.cumsum([len(t) for t in word_tokens[:-1]]), (1, 0))
    per_word = []
    for w, i, j in zip(words[:-1], bounds[:-1], bounds[1:]):
        chunk = probs[i:j]
        per_word.append((w, float(np.mean(chunk)) if chunk else float("nan")))
    return total, per_word


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rec_id, rest = sys.argv[1], sys.argv[2:]
    extra: list[str] = []
    if rest[:1] == ["--with"]:
        extra = [t.strip() for t in rest[1].split(",") if t.strip()]
        rest = rest[2:]
    candidates = rest
    rec = find_record(rec_id)
    saved = load_wav(Path(rec["wav"]))
    peak, rms = audio_mod.loudness(saved)
    # The wav on disk is the microphone as it was; the recognizer got it pulled
    # up to a working level (audio.normalize), exactly as the app does.
    audio = audio_mod.normalize(saved)
    print(f"take {rec_id}: {len(audio)/16000:.2f} s of audio, peak {peak:.3f}, rms {rms:.4f}, "
          f"fed to the recognizer x{float(np.abs(audio).max())/max(peak,1e-9):.2f}")
    print(f"logged raw:   {rec.get('raw')!r}")
    print(f"logged final: {rec.get('final')!r}")

    cfg = cfg_mod.load()
    hint = app_hint(cfg, extra)
    if extra:
        print(f"hint starts with the extra words: {', '.join(extra)}")
    asr = Asr(cfg, [])
    asr.prompt = hint
    print(f"model {asr.model_name} on {asr.device}/{asr.compute_type}, "
          f"beam {asr.beam_size}, vad {asr.vad}, hint {len(hint)} chars: {hint[:90]!r}...")
    asr.load()
    asr.warmup()

    print("\n== decodes ==")
    same, _ = asr.transcribe(audio)
    print(f"app settings:      {same!r}")
    print(f"as saved, no gain: {asr._run(saved, hint)!r}")
    print(f"without the hint:  {asr._run(audio, None)!r}")
    beam = asr.beam_size
    asr.beam_size = 1
    print(f"beam 1 with hint:  {asr._run(audio, hint)!r}")
    asr.beam_size = beam
    asr.vad = False
    print(f"no VAD, with hint: {asr._run(audio, hint)!r}")
    asr.vad = bool(cfg["asr"].get("vad", True))

    print("\n== word confidence of the app's own answer ==")
    for w, p in word_confidence(asr, audio, hint):
        print(f"  {p:5.2f}  {w}")

    tok = Tokenizer(asr.model.hf_tokenizer, True, task="transcribe", language=asr.language)
    texts = [same] + [c for c in candidates if c and c != same]
    for label, prompt in (("with the hint", hint), ("without the hint", None)):
        print(f"\n== forced scoring, {label} (log-prob; higher is likelier) ==")
        for t in texts:
            total, per_word = forced_scores(asr, tok, audio, t, prompt)
            words = "  ".join(f"{w.strip()}={p:.2f}" for w, p in per_word)
            print(f"  {total:8.2f}  {t!r}\n            {words}")


if __name__ == "__main__":
    main()
