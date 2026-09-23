# -*- coding: utf-8 -*-
"""Re-run the last N takes through both recognizer models, with and without the hint.

    ..\.venv\Scripts\python.exe bench\retranscribe_last.py <takes.json> <out.json>

Loads its own copy of the two Whisper models (about 5 GB of video memory).
Does not touch LM Studio.
"""
import json, sys, time, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod, termstats, audio as audio_mod
from stt.fixes import Fixes
from stt.asr import Asr

src, dst = sys.argv[1], sys.argv[2]
takes = json.load(open(src, encoding="utf-8"))
cfg = cfg_mod.load(); terms = cfg_mod.glossary(); fixes = Fixes(cfg_mod.FIXES_PATH); mywords = cfg_mod.mywords()
hint = termstats.rank(terms, cfg_mod.LOG_DIR, aliases=termstats.aliases_from_fixes(fixes),
                      days=int(cfg.get("asr", {}).get("prompt_days", 14)), skip=mywords)
asr = Asr(cfg, hint)
print("loading models...", flush=True); t = asr.load(); print(f"loaded in {t:.1f}s; hint: {asr.prompt[:200]}", flush=True)

def read(p):
    w = wave.open(p); sr = w.getframerate(); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768; w.close()
    assert sr == 16000, sr
    return x

out = []
for r in takes:
    p = r.get("wav")
    if not p or not Path(p).exists():
        out.append({**r, "err": "no wav"}); continue
    x = audio_mod.normalize(read(p))
    row = {k: r.get(k) for k in ("id", "seconds_audio", "preroll_s", "asr_model", "raw", "final")}
    for mname, m in (("large-v3-turbo", asr.model), ("large-v3", asr.short)):
        for hname, pr in (("hint", asr.prompt), ("nohint", None)):
            t0 = time.perf_counter()
            try: txt = asr._run(x, pr, m)
            except Exception as e: txt = f"ERR {e}"
            row[f"{mname}|{hname}"] = txt; row[f"ms|{mname}|{hname}"] = int((time.perf_counter() - t0) * 1000)
    out.append(row)
    print(r["id"], "ok", flush=True)
json.dump({"hint": asr.prompt, "rows": out}, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done", dst)
