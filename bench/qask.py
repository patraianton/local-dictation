# -*- coding: utf-8 -*-
"""Спросить правщик прямо: «это вопрос?» — и посчитать, насколько он прав.

Сейчас вопросительный знак берётся из того, как правщик расставил знаки
препинания, когда его просили «причесать текст». Это побочный продукт: на
приказах вроде «Покажи мне скриншот» он лепит знак вопроса, а настоящий
вопрос без вопросительного слова пропускает.

Здесь проверяется другой способ: отдельный короткий вопрос к той же модели,
с правилами и примерами, ответ «да» или «нет». Ответы сохраняются, чтобы
сравнить их с разметкой живого человека и понять, стоит ли переходить.

Модель берётся та, что лежит в памяти LM Studio. Запросы идут по одному.

    ..\\.venv\\Scripts\\python.exe qask.py                  # по дампу qeval-dump.json
    ..\\.venv\\Scripts\\python.exe qask.py --out qask.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt import polish as P  # noqa: E402

HERE = Path(__file__).resolve().parent

# Правила написаны по разбору настоящих ошибок правщика (08-10.09.2026):
# он ставил знак вопроса на приказах «Покажи мне скриншот», «Пришли ссылки»
# и на придаточных «объясняю, при чём тут мои успехи».
SYSTEM = """Ты определяешь, задал ли человек ВОПРОС.

Тебе дают одну фразу из голосовой диктовки. Знаки препинания в ней
расставлены машиной и могут быть неверными — смотри на смысл и на слова.

Ответь ровно одним словом: ДА или НЕТ.

ДА — человек ждёт ОТВЕТА:
  «Почему упало» — ДА
  «Ты разобрался, почему упало» — ДА
  «Скажи, ты читал этот документ» — ДА (он ждёт ответа, а не действия)
  «Как скоро мы это закончим» — ДА
  «Готово» — ДА, если это переспрос; НЕТ, если это отчёт. Без иного — НЕТ
  «Перезайти не можешь, нет» — ДА
  «Это в купере нельзя или он тупой» — ДА

НЕТ — человек даёт УКАЗАНИЕ, рассуждает или сообщает:
  «Покажи мне скриншот, который соответствует спекам» — НЕТ (приказ)
  «Разберись, почему упало» — НЕТ (приказ)
  «Объясняю, при чём тут мои успехи» — НЕТ
  «Вопрос не в том, что случилось, а в том, что делать» — НЕТ
  «Надо в Notion сделать» — НЕТ
  «Мы ссылки сделали на эти статьи» — НЕТ (утверждение)

Отвечай одним словом: ДА или НЕТ."""

SENT = re.compile(r"[^.!?…]+[.!?…]*")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.findall(text or "") if s.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=str(HERE / "qeval-dump.json"))
    ap.add_argument("--out", default=str(HERE / "qask.json"))
    ap.add_argument("--pause", type=float, default=0.03)
    ap.add_argument("--field", default="final",
                    help="по какому тексту резать фразы: final или raw")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    pol = P.Polisher(cfg, [], None)
    if not pol.check(force=True):
        print(f"[X] правщик недоступен: {pol.reason}")
        sys.exit(1)
    print(f"[.] модель: {pol.model or '(та, что в памяти)'}")

    rows = json.loads(Path(args.dump).read_text(encoding="utf-8"))
    out, asked, t0 = [], 0, time.perf_counter()
    for n, r in enumerate(rows, 1):
        parts = sentences(r.get(args.field, "")) or sentences(r.get("raw", ""))
        answers = []
        for s in parts:
            body = {
                "model": pol.model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": s.rstrip(".!?… ")},
                ],
                "temperature": 0.0,
                "max_tokens": 3,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            try:
                resp = pol._client.post(
                    f"{pol.base}/v1/chat/completions", json=body, timeout=20.0
                )
                resp.raise_for_status()
                a = resp.json()["choices"][0]["message"]["content"]
                a = P._strip_think(a).strip().lower()
            except Exception as exc:
                a = f"failed:{type(exc).__name__}"
            answers.append({"sentence": s, "answer": a,
                            "yes": a.startswith(("да", "yes"))})
            asked += 1
            if args.pause:
                time.sleep(args.pause)
        out.append({"id": r.get("id", ""), "i": n - 1,
                    "raw": r.get("raw", ""), "final": r.get("final", ""),
                    "sentences": answers,
                    "any_yes": any(a["yes"] for a in answers)})
        if n % 25 == 0 or n == len(rows):
            print(f"    {n}/{len(rows)}  фраз {asked}  ({time.perf_counter()-t0:.0f} c)")

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"[v] сохранено: {args.out}  (фраз спрошено: {asked})")


if __name__ == "__main__":
    main()
