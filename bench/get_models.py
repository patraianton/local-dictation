# -*- coding: utf-8 -*-
"""Качаем модели-корректоры прямо в папку LM Studio (с докачкой при обрыве)."""
import sys
import time
from pathlib import Path

from huggingface_hub import hf_hub_download

LM_MODELS = Path(r"C:\Users\panto\.lmstudio\models")

WANTED = [
    ("lmstudio-community/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q6_K.gguf"),
    ("lmstudio-community/Qwen3-8B-GGUF", "Qwen3-8B-Q6_K.gguf"),
    ("lmstudio-community/gemma-3-12b-it-GGUF", "gemma-3-12b-it-Q4_K_M.gguf"),
]


def main() -> None:
    for repo, fname in WANTED:
        publisher, name = repo.split("/")
        target = LM_MODELS / publisher / name
        target.mkdir(parents=True, exist_ok=True)
        if (target / fname).exists():
            mb = (target / fname).stat().st_size / 1024 / 1024
            print(f"[есть] {fname} ({mb:.0f} МБ)", flush=True)
            continue
        for attempt in range(1, 6):
            try:
                print(f"[качаю] {fname} (попытка {attempt})", flush=True)
                t0 = time.time()
                hf_hub_download(repo_id=repo, filename=fname, local_dir=str(target))
                mb = (target / fname).stat().st_size / 1024 / 1024
                print(f"[готово] {fname} — {mb:.0f} МБ за {time.time()-t0:.0f} с", flush=True)
                break
            except Exception as exc:
                print(f"[сбой] {type(exc).__name__}: {exc}", flush=True)
                time.sleep(5)
        else:
            print(f"[БРОСИЛ] {fname}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
