# -*- coding: utf-8 -*-
"""Full evaluation of setting variants over the whole history.

The ElevenLabs text is used as the reference (a paid service, used in anger).
It is not perfect truth, but a strong anchor: a large difference is a real
difference.
"""
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import cuda_fix  # noqa: E402

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402
from spokenly import records  # noqa: E402

OUT = Path(__file__).resolve().parent / "eval-result.json"

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Подсказка-список: сухое перечисление терминов.
PROMPT_LIST = (
    "Термины и названия: Claude Code, Codex, Opus, Sonnet, TimelinesAI, WhatsApp, "
    "Intercom, Slack, Linear, Mailflow, HeyReach, PostHog, Vercel, GitHub, worktree, "
    "loop, signup, deploy, prompt, API, CRM, MRR, churn, team-ops, autopase."
)
# Подсказка-пример: живая фраза в том стиле, в каком он говорит.
PROMPT_SAMPLE = (
    "Окей, смотри: закинь этот worktree в Claude Code, поставь loop на пять часов, "
    "потом глянь Intercom и Mailflow, и обнови дашборд в PostHog. "
    "По signups и MRR за неделю дай отдельную табличку."
)


def norm(text: str) -> list[str]:
    text = text.lower().replace("ё", "е")
    text = PUNCT_RE.sub(" ", text)
    return text.split()


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """(число ошибок, длина образца) — расстояние Левенштейна по словам."""
    r, h = norm(ref), norm(hyp)
    if not r:
        return (len(h), 0)
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (r[i - 1] != h[j - 1]),
            )
        prev = cur
    return prev[len(h)], len(r)


def person_flips(ref: str, hyp: str) -> tuple[int, int]:
    """(потеряно приказов, придумано приказов) против образца.

    «Потеряно» — самое вредное: у ElevenLabs «сделай», у нас «сделаю»,
    и агент читает поручение как обещание.
    """
    import difflib

    from stt.endings import PAIRS

    a, b = norm(ref), norm(hyp)
    lost = added = 0
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
            continue
        r, h = a[i1], b[j1]
        if PAIRS.get(h) == r:      # образец: приказ, у нас: о себе
            lost += 1
        elif PAIRS.get(r) == h:    # образец: о себе, у нас: приказ
            added += 1
    return lost, added


SENT_RE = re.compile(r"[^.!?…]+[.!?…]*")


def question_marks(ref: str, hyp: str) -> tuple[int, int, int]:
    """(вопросов узнано, вопрос потерян, точка стала вопросом).

    «Потерян» — вредно: агент читает вопрос как утверждение и не отвечает.
    """
    def sents(t):
        return [s.strip() for s in SENT_RE.findall(t or "") if s.strip()]

    def key(s):
        return " ".join(norm(s))

    ref_map = {key(s): s.rstrip().endswith("?") for s in sents(ref)}
    ok = lost = added = 0
    for s in sents(hyp):
        k = key(s)
        if not k or k not in ref_map:
            continue
        mine = s.rstrip().endswith("?")
        if ref_map[k] and mine:
            ok += 1
        elif ref_map[k] and not mine:
            lost += 1
        elif mine and not ref_map[k]:
            added += 1
    return ok, lost, added


def looped(text: str, times: int = 4) -> bool:
    """Признак срыва в повтор: одна и та же тройка слов много раз подряд."""
    w = norm(text)
    if len(w) < times * 3:
        return False
    grams = {}
    for i in range(len(w) - 2):
        g = tuple(w[i : i + 3])
        grams[g] = grams.get(g, 0) + 1
        if grams[g] >= times:
            return True
    return False


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr, n_ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        pcm = pcm.reshape(-1, n_ch).mean(axis=1)
    if sr != 16000:
        import soxr

        pcm = soxr.resample(pcm, sr, 16000).astype(np.float32)
    return pcm


PROMPT_PHRASES = (
    "Сделай session handover. Продолжай, не останавливайся. "
    "Запускай то, что оборвалось. Отправляй агентов и собери отчёт. "
    "Проверь Intercom и обнови дашборд. " + PROMPT_LIST
)
# Только глаголы, без содержательных слов — они не должны всплывать в тексте.
PROMPT_VERBS = (
    "Сделай, проверь, посмотри, запусти, запускай, отправляй, продолжай, "
    "отгружай, собери, поставь, обнови, покажи, найди, добавь, открой. "
    + PROMPT_LIST
)

PROMPT_ASK = (
    "Почему так вышло? Ты проверил? Мы заливаем статьи уже? Сколько осталось? "
    "Забьём на это? В смысле? " + PROMPT_VERBS
)

VARIANTS = {
    "глаголы + термины (сейчас)": dict(
        model="large-v3-turbo", prompt=PROMPT_VERBS, guard=True, beam=5
    ),
    "вопросы + глаголы + термины": dict(
        model="large-v3-turbo", prompt=PROMPT_ASK, guard=True, beam=5
    ),
}


def run_variant(name: str, spec: dict, items: list, refs: list[str]) -> dict:
    model = WhisperModel(spec["model"], device="cuda", compute_type="float16")
    model.transcribe(np.zeros(16000, dtype=np.float32), language="ru", beam_size=1)

    kw = dict(
        language="ru",
        beam_size=int(spec.get("beam", 1)),
        condition_on_previous_text=False,
        initial_prompt=spec["prompt"] or None,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        without_timestamps=True,
    )
    if spec.get("guard"):
        # Защита от срыва в повтор: даём модели отступить на большей температуре
        # и штрафуем повторение.
        kw["temperature"] = [0.0, 0.2, 0.4, 0.6]
        kw["compression_ratio_threshold"] = 2.4
        kw["repetition_penalty"] = 1.15
        kw["no_repeat_ngram_size"] = 0
    else:
        kw["temperature"] = 0.0

    errs = tot = lost_cmd = added_cmd = 0
    q_ok = q_lost = q_added = 0
    loops = []
    times = []
    texts = []
    for i, audio in enumerate(items):
        t0 = time.perf_counter()
        segs, _ = model.transcribe(audio, **kw)
        text = " ".join(s.text.strip() for s in segs).strip()
        dt = time.perf_counter() - t0
        # Вторая попытка без подсказки, если модель сорвалась в повтор.
        if spec.get("guard") and looped(text):
            kw2 = dict(kw)
            kw2["initial_prompt"] = None
            segs, _ = model.transcribe(audio, **kw2)
            text = " ".join(s.text.strip() for s in segs).strip()
            dt = time.perf_counter() - t0
        times.append(dt)
        texts.append(text)
        if looped(text):
            loops.append(i)
        e, n = wer(refs[i], text)
        errs += e
        tot += n
        dl, da = person_flips(refs[i], text)
        lost_cmd += dl
        added_cmd += da
        qo, ql, qa = question_marks(refs[i], text)
        q_ok += qo
        q_lost += ql
        q_added += qa
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(items)}")
    del model
    return {
        "wer": errs / max(1, tot),
        "loops": loops,
        "lost_commands": lost_cmd,
        "added_commands": added_cmd,
        "q_ok": q_ok,
        "q_lost": q_lost,
        "q_added": q_added,
        "sec_total": sum(times),
        "sec_avg": sum(times) / len(times),
        "texts": texts,
    }


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    recs = [r for r in records() if r["wav"] and r["text"] and r["seconds"] >= 1.0]
    recs.sort(key=lambda r: r["created"])
    if limit:
        recs = recs[:limit]
    print(f"записей: {len(recs)}, речи: {sum(r['seconds'] for r in recs)/60:.1f} мин")

    audios = [load_wav(r["wav"]) for r in recs]
    refs = [r["text"] for r in recs]

    report = {}
    for name, spec in VARIANTS.items():
        print(f"\n=== {name} ===")
        res = run_variant(name, spec, audios, refs)
        report[name] = res
        print(f"  ошибок против ElevenLabs: {res['wer']*100:.1f}%")
        print(f"  приказ превращён в «я сделаю»: {res['lost_commands']}")
        print(f"  наоборот, придуман приказ:     {res['added_commands']}")
        print(f"  срывов в повтор: {len(res['loops'])}")
        print(f"  время: {res['sec_avg']:.2f} с на реплику")

    OUT.write_text(
        json.dumps(
            {
                "records": [
                    {"id": r["id"], "date": r["date"], "seconds": r["seconds"],
                     "elevenlabs": r["text"], "wav": str(r["wav"])}
                    for r in recs
                ],
                "variants": report,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\nподробности: {OUT}")
    print("\n--- итог ---")
    for name, res in report.items():
        print(f"{name:<26} ошибок {res['wer']*100:5.1f}% | "
              f"приказ потерян {res['lost_commands']:>2} | "
              f"вопрос потерян {res['q_lost']:>2} (узнан {res['q_ok']:>2}, "
              f"придуман {res['q_added']:>2}) | {res['sec_avg']:.2f} с")


if __name__ == "__main__":
    main()
