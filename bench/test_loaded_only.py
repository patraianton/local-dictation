# -*- coding: utf-8 -*-
"""Диктовка работает только с той моделью, что уже лежит в памяти.

Правило хозяина (20.08.2026): загруженная им модель нужна ему для работы, она
должна оставаться, и никакой второй модели в видеопамяти быть не должно. Если
попросить у LM Studio модель, которая просто скачана, — она начнёт её грузить.
Здесь LM Studio подделан, ничего настоящего не запускается.

    ..\\.venv\\Scripts\\python.exe test_loaded_only.py
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import Polisher  # noqa: E402

BIG = "qwen/qwen3-30b-a3b-2507"     # скачана, но выгружена
SMALL = "qwen3-4b-instruct-2507"    # лежит в памяти, ей и работать
OTHER = "gemma-3-12b-it"            # тоже только скачана

state = {
    "loaded": [SMALL],
    "api_v0": True,       # False — старая LM Studio, у неё нет /api/v0
    "chat_calls": [],     # тела запросов на генерацию
}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v0/models":
            if not state["api_v0"]:
                return self._send(404, {"error": "not found"})
            data = [
                {"id": m, "type": "llm",
                 "state": "loaded" if m in state["loaded"] else "not-loaded"}
                for m in (BIG, SMALL, OTHER)
            ]
            data.append({"id": "text-embedding-nomic", "type": "embeddings",
                         "state": "loaded"})
            return self._send(200, {"data": data})
        if self.path == "/v1/models":
            # Настоящая LM Studio отдаёт здесь ВСЁ скачанное, без признака
            # «в памяти» — потому по этому списку и нельзя выбирать.
            return self._send(200, {"data": [{"id": m} for m in (BIG, SMALL, OTHER)]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        state["chat_calls"].append(body)
        text = body["messages"][-1]["content"]
        self._send(200, {"choices": [{"message": {"content": text}}]})


def make(model: str = "") -> Polisher:
    cfg = {"polish": {"url": f"http://127.0.0.1:{PORT}", "model": model,
                      "min_words": 0, "timeout_s": 2.0}}
    return Polisher(cfg, [])


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case("в списке только то, что в памяти — скачанное не предлагаем")
def _():
    got, why = make().loaded_models()
    return got == [SMALL], f"{got} {why}"


@case("выбирается загруженная модель, а не первая из скачанных")
def _():
    pol = make()
    pol.check(force=True)
    return pol.model == SMALL, pol.model


@case("модель из настроек выгружена — берём ту, что в памяти")
def _():
    pol = make(model=BIG)
    pol.check(force=True)
    return pol.model == SMALL, pol.model


@case("в запрос уходит загруженная модель и никакого ttl")
def _():
    state["chat_calls"].clear()
    pol = make(model=BIG)
    pol.check(force=True)
    pol.polish("проверка связи")
    if not state["chat_calls"]:
        return False, "запроса не было"
    body = state["chat_calls"][-1]
    return body["model"] == SMALL and "ttl" not in body, str(body)[:80]


@case("модель подменили — следующая надиктовка идёт в новую")
def _():
    pol = make()
    pol.check(force=True)
    state["loaded"] = [OTHER]
    pol._loaded_seen = 0.0  # как будто прошло больше 20 секунд
    state["chat_calls"].clear()
    pol.polish("проверка связи")
    state["loaded"] = [SMALL]
    ok = bool(state["chat_calls"]) and state["chat_calls"][-1]["model"] == OTHER
    return ok, str(state["chat_calls"])[:80]


@case("в памяти пусто — корректор молчит и НИЧЕГО не просит загрузить")
def _():
    pol = make(model=BIG)
    state["loaded"] = []
    state["chat_calls"].clear()
    ok_check = not pol.check(force=True)
    text, _sec, note = pol.polish("проверка связи")
    state["loaded"] = [SMALL]
    return (ok_check and not state["chat_calls"] and text == "проверка связи",
            f"{note} | запросов: {len(state['chat_calls'])}")


@case("одна только модель для поиска (embeddings) — это не корректор")
def _():
    state["loaded"] = ["text-embedding-nomic"]
    got, why = make().loaded_models()
    state["loaded"] = [SMALL]
    return got == [], f"{got} {why}"


@case("старая LM Studio без /api/v0 — работаем по-прежнему")
def _():
    state["api_v0"] = False
    got, _why = make().loaded_models()
    state["api_v0"] = True
    return got == sorted([BIG, SMALL, OTHER]), str(got)


def main() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        bad += not ok
        print(("[v] " if ok else "[X] ") + name)
        if not ok:
            print("    ", detail)
    print(f"\n{len(CASES) - bad} of {len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        raise SystemExit(main())
    finally:
        srv.shutdown()
