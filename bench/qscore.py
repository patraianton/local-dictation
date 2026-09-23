# -*- coding: utf-8 -*-
"""Слышно ли вопрос в голосе: спрашиваем саму распознавалку, а не текст.

Распознавалка пишет знак в конце сама, одним «лучшим» вариантом: как решила,
так и осталось. Но её можно спросить иначе — дать ей ДВА готовых варианта
одной и той же фразы, с точкой и с вопросительным знаком, и заставить
прочитать оба под запись («учительское чтение»). Она вернёт, насколько
каждый вариант согласуется со звуком. Разница между двумя числами и есть
ответ на вопрос «подняла ли она голос в конце».

Это не разбор высоты голоса руками (он уже проверен и слаб, см. pitch.py):
здесь работает та же самая нейросеть, которая уже умеет слышать интонацию,
просто её спрашивают прямо.

Для каждой надиктовки печатается и сохраняется:
    d = log P(вариант с «?») - log P(вариант с «.»)
Больше нуля — звук больше похож на вопрос.

Грузит СВОЮ копию распознавалки (turbo, ~1,6 ГБ видеопамяти), как и why.py.
Живой диктовке не мешает: LM Studio не трогает.

    ..\\.venv\\Scripts\\python.exe qscore.py 2026-09-08 2026-09-10
"""
import argparse
import glob
import json
import math
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cuda_fix  # noqa: E402

cuda_fix.enable()

from faster_whisper.audio import pad_or_trim  # noqa: E402
from faster_whisper.tokenizer import Tokenizer  # noqa: E402

from stt import config as cfg_mod  # noqa: E402
from stt.asr import Asr  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MARKS = ".!?…"


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        sr, ch = wf.getframerate(), wf.getnchannels()
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        import soxr

        a = soxr.resample(a, sr, 16000).astype(np.float32)
    return a


def with_mark(text: str, mark: str) -> str:
    t = text.rstrip()
    while t and t[-1] in MARKS:
        t = t[:-1].rstrip()
    return t + mark


def forced_logprob(asr: Asr, tok: Tokenizer, enc, n_frames: int, text: str) -> float:
    """Насколько модель согласна, что в записи сказано именно это."""
    text_tokens = tok.encode(" " + text.strip())
    res = asr.model.model.align(
        enc, list(tok.sot_sequence), [text_tokens], n_frames, median_filter_width=7
    )[0]
    return sum(math.log(max(p, 1e-9)) for p in res.text_token_probs)


def takes(since: str, until: str) -> list[dict]:
    rows = []
    for p in sorted(glob.glob(str(ROOT / "logs" / "2026-*.jsonl"))):
        if not (since <= Path(p).stem <= until):
            continue
        for line in open(p, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("raw", "").strip() and r.get("wav"):
                rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("since")
    ap.add_argument("until")
    ap.add_argument("--out", default=str(HERE / "qscore.json"))
    ap.add_argument("--max-seconds", type=float, default=29.0,
                    help="записи длиннее не берём: модель читает окно в 30 с")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    asr = Asr(cfg, [])
    took = asr.load()
    print(f"[.] распознавалка {asr.model_name} на {asr.device}: {took:.1f} с")
    tok = Tokenizer(asr.model.hf_tokenizer, asr.model.model.is_multilingual,
                    task="transcribe", language=asr.language)

    rows = takes(args.since, args.until)
    print(f"[.] надиктовок: {len(rows)}")
    out, t0, skipped = [], time.perf_counter(), 0
    for n, r in enumerate(rows, 1):
        wav = Path(r["wav"])
        if not wav.exists() or float(r.get("seconds_audio", 0)) > args.max_seconds:
            skipped += 1
            continue
        try:
            audio = load_wav(wav)
            features = asr.model.feature_extractor(audio)
            n_frames = features.shape[-1]
            enc = asr.model.encode(pad_or_trim(features))
            raw = r["raw"]
            dot = forced_logprob(asr, tok, enc, n_frames, with_mark(raw, "."))
            qst = forced_logprob(asr, tok, enc, n_frames, with_mark(raw, "?"))
        except Exception as exc:
            skipped += 1
            continue
        out.append({
            "id": r.get("id", ""), "time": r.get("time", ""),
            "seconds_audio": r.get("seconds_audio", 0),
            "raw": raw, "final": r.get("final", ""),
            "heard_q": "?" in raw,
            "logp_dot": dot, "logp_q": qst, "d": qst - dot,
        })
        if n % 25 == 0 or n == len(rows):
            print(f"    {n}/{len(rows)}  ({time.perf_counter()-t0:.0f} с)")

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"[v] сохранено: {args.out}  (посчитано {len(out)}, пропущено {skipped})")


if __name__ == "__main__":
    main()
