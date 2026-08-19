# -*- coding: utf-8 -*-
"""Проверка выбора модели корректора на странице.

    ..\\.venv\\Scripts\\python.exe test_models_api.py

LM Studio для этого не нужен: корректор подделан. Проверяется то, что делает
страница — список моделей, переключение, запись выбора в config.toml.
"""
import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import config as cfg_mod  # noqa: E402
from stt import server as srv  # noqa: E402

SANDBOX_CONFIG = "\n".join([
    "[asr]",
    'model = "large-v3-turbo"',
    "",
    "[polish]",
    "# комментарий, который нельзя потерять",
    'model = "старая"',
    'mode = "light"',
    "",
])


class FakePolisher:
    """Корректор без LM Studio."""

    def __init__(self, available, model="", why=""):
        self.available_list = available
        self.model = model
        self.why = why
        self.enabled = True

    def list_models(self):
        return (list(self.available_list), self.why)

    def use_model(self, name):
        if name not in self.available_list:
            return False
        self.model = name
        return True


def call(port, path, body=None):
    url = f"http://127.0.0.1:{port}{path}"
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def serve(polisher):
    srv.Handler.fixes = None
    srv.Handler.on_terms = None
    srv.Handler.polisher = polisher
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main() -> None:
    bad = 0
    # Настоящий config.toml трогать нельзя: маршрут /api/model пишет выбор прямо
    # в него. Подменяем путь СРАЗУ, до первого запроса — иначе проверка меняет
    # рабочие настройки Антона (уже случилось однажды).
    real_cfg = cfg_mod.CONFIG_PATH
    tmpdir = Path(tempfile.mkdtemp())
    sandbox = tmpdir / "config.toml"
    sandbox.write_text(SANDBOX_CONFIG, encoding="utf-8")
    cfg_mod.CONFIG_PATH = sandbox

    def check(ok, why, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok and detail:
            print(f"      {detail}")

    try:
        # --- список моделей ---
        pol = FakePolisher(["qwen/qwen3-30b", "qwen/qwen3.8-27b"], "qwen/qwen3-30b")
        httpd, port = serve(pol)
        code, data = call(port, "/api/models")
        check(code == 200
              and data["available"] == ["qwen/qwen3-30b", "qwen/qwen3.8-27b"],
              "страница видит все загруженные модели", str(data))
        check(data["current"] == "qwen/qwen3-30b",
              "показывает, какая сейчас в работе", str(data.get("current")))

        # --- переключение ---
        code, data = call(port, "/api/model", {"model": "qwen/qwen3.8-27b"})
        check(code == 200 and data["ok"] and pol.model == "qwen/qwen3.8-27b",
              "переключает корректора на выбранную модель", f"{code} {data}")
        check(data.get("saved") is True,
              "и сразу записывает выбор в настройки", str(data))

        code, data = call(port, "/api/model", {"model": "нет-такой"})
        check(code == 400 and not data.get("ok"),
              "несуществующую модель не принимает", f"{code} {data}")

        code, _d = call(port, "/api/model", {"model": ""})
        check(code == 400, "пустое имя не принимает")
        httpd.shutdown()

        # --- LM Studio выключен ---
        pol2 = FakePolisher([], "", why="LM Studio is not answering (ConnectTimeout)")
        httpd, port = serve(pol2)
        code, data = call(port, "/api/models")
        check(code == 200 and data["available"] == [] and "LM Studio" in data["why"],
              "с выключенным LM Studio отвечает понятной причиной, а не падает",
              str(data))
        httpd.shutdown()

        # --- корректор вообще не подключён ---
        httpd, port = serve(None)
        code, data = call(port, "/api/models")
        check(code == 200 and data["available"] == [],
              "без корректора страница не ломается")
        code, _d = call(port, "/api/model", {"model": "что-нибудь"})
        check(code == 400, "и переключать нечего")
        httpd.shutdown()

        # --- что именно осталось в настройках ---
        text = sandbox.read_text(encoding="utf-8")
        check('model = "qwen/qwen3.8-27b"' in text,
              "выбор переживает перезапуск: лежит в config.toml")
        check('model = "large-v3-turbo"' in text,
              "модель распознавалки при этом не тронута")
        check("# комментарий, который нельзя потерять" in text,
              "объяснения в настройках остаются на месте")
    finally:
        cfg_mod.CONFIG_PATH = real_cfg
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{'всё сошлось' if not bad else str(bad) + ' не сошлось'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
