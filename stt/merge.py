# -*- coding: utf-8 -*-
"""Two readings of one take, the better one chosen place by place (03.11.2026).

Why. On the 133 takes of 27-30.10.2026 where the cloud and the local recognizer
disagreed, a text taken from both — each disputed place from whichever reading
made sense there — was judged better than the cloud's alone in 59 takes and
worse in 1 (blind, six judges; 3 errors against 78). The two make different
mistakes: the cloud writes "ангдиски" where the local one has "английский", the
local one writes "Пояснях" where the cloud has "Поясни".

How. The two texts are lined up word by word; where they differ, a strong model
is shown both pieces in context and answers only which one — "A" or "B" — for
each place. It cannot write anything of its own, and it answers in a few
tokens, so the wait stays short. Anything that fails — no key, no answer, a
slow answer, an answer that is not the list asked for — and the cloud's text
stays as it was.
"""
import difflib
import json
import os
import re
import time
from pathlib import Path

import httpx

# Bedrock, through an API key (the "ABSK..." kind): a bearer token, no signing.
URL = "https://bedrock-runtime.{region}.amazonaws.com/model/{model}/converse"
# One place too many and the prompt is mostly brackets; such a take keeps the
# cloud's text (measured: the 133 disputed takes had 1-21 places, median 3).
MAX_PLACES = 30

SYSTEM = (
    "You repair speech recognition. Two recognizers wrote down the same recording "
    "of a Russian speaker who dictates to coding agents and mixes in English tech "
    "terms and product names. You get reading A with the places where reading B "
    "differs marked as [k: A-text | B-text] (∅ means nothing there). For each place "
    "choose the variant that is more likely what was really said: real words, "
    "grammar a native speaker uses, the meaning of the sentence, correct spelling "
    "of the speaker's terms. Reply with JSON only, like {\"1\":\"A\",\"2\":\"B\"}."
)


def _key(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower().replace("ё", "е"))


def places(a: str, b: str) -> tuple[list[str], list[str], list[tuple[int, int, int, int]]]:
    """The two texts as words, and where they differ (by words, not by marks)."""
    ta, tb = a.split(), b.split()
    ka = [_key(t) for t in ta]
    kb = [_key(t) for t in tb]
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    out = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    return ta, tb, out


def marked(ta: list[str], tb: list[str], diff) -> str:
    """Reading A with every disputed place written as [k: A | B]."""
    parts, last = [], 0
    for k, (i1, i2, j1, j2) in enumerate(diff, 1):
        parts.extend(ta[last:i1])
        left = " ".join(ta[i1:i2]) or "∅"
        right = " ".join(tb[j1:j2]) or "∅"
        parts.append(f"[{k}: {left} | {right}]")
        last = i2
    parts.extend(ta[last:])
    return " ".join(parts)


def apply(ta: list[str], tb: list[str], diff, choice: dict) -> str:
    """Reading A with the places the answer gave to B taken from B."""
    words, last = [], 0
    for k, (i1, i2, j1, j2) in enumerate(diff, 1):
        words.extend(ta[last:i1])
        words.extend(tb[j1:j2] if str(choice.get(str(k), "A")).strip().upper() == "B" else ta[i1:i2])
        last = i2
    words.extend(ta[last:])
    return " ".join(words)


def parse_choice(text: str, n: int) -> dict | None:
    """The answer as {"1": "A", ...}, or None when it is not that."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    out = {}
    for k, v in d.items():
        if str(k).isdigit() and 1 <= int(k) <= n and str(v).strip().upper() in ("A", "B"):
            out[str(int(k))] = str(v).strip().upper()
    return out or None


def read_key(cfg_m: dict) -> str:
    key = os.environ.get("BEDROCK_API_KEY", "").strip()
    if key:
        return key
    path = Path(os.path.expanduser(str(cfg_m.get("key_file", "~/.secrets/speech-cloud.env"))))
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "BEDROCK_API_KEY" and value.strip():
                return value.strip()
    except OSError:
        pass
    return ""


class Merger:
    def __init__(self, cfg: dict):
        m = cfg.get("merge", {}) or {}
        self.enabled = bool(m.get("enabled", False))
        self.min_seconds = float(m.get("min_seconds", 20.0))
        self.region = str(m.get("region", "eu-west-1"))
        # Tried in turn: the cross-region profiles of this key are refused now
        # and then in one region or another (403 "Marketplace"), 03.11.2026.
        self.models = [str(x) for x in m.get("models", ["eu.anthropic.claude-sonnet-5-5"])]
        self.timeout_s = float(m.get("timeout_s", 4.0))
        self.key = read_key(m) if self.enabled else ""
        self.terms: list[str] = []
        self.last: dict | None = None

    def ready(self) -> bool:
        return self.enabled and bool(self.key)

    def set_terms(self, terms: list[str]) -> None:
        self.terms = [t for t in terms if t][:100]

    def _ask(self, prompt: str, deadline: float) -> tuple[str, str]:
        body = {
            "system": [{"text": SYSTEM}],
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 400},
        }
        err = ""
        for model in self.models:
            left = deadline - time.perf_counter()
            if left <= 0.2:
                break
            try:
                r = httpx.post(URL.format(region=self.region, model=model.replace(":", "%3A")),
                               headers={"Authorization": f"Bearer {self.key}"},
                               json=body, timeout=left)
            except Exception as exc:
                err = f"{type(exc).__name__}"
                continue
            if r.status_code != 200:
                err = f"HTTP {r.status_code}"
                continue
            parts = r.json().get("output", {}).get("message", {}).get("content", [])
            return "".join(p.get("text", "") for p in parts), model
        raise RuntimeError(err or "no model answered")

    def merge(self, cloud: str, local: str) -> str:
        """The cloud's text with each disputed place settled; the cloud's text on any failure."""
        t0 = time.perf_counter()
        ta, tb, diff = places(cloud, local)
        self.last = {"places": len(diff)}
        if not diff:
            return cloud
        if len(diff) > MAX_PLACES:
            self.last["skipped"] = "too many places"
            return cloud
        prompt = ("Speaker's terms: " + ", ".join(self.terms) + "\n\n" if self.terms else "") \
            + "Reading A with the differences of reading B:\n" + marked(ta, tb, diff)
        try:
            text, model = self._ask(prompt, t0 + self.timeout_s)
        except Exception as exc:
            self.last.update(error=str(exc)[:120], s=round(time.perf_counter() - t0, 2))
            return cloud
        choice = parse_choice(text, len(diff))
        self.last.update(s=round(time.perf_counter() - t0, 2), model=model)
        if choice is None:
            self.last["error"] = "answer not understood"
            return cloud
        self.last["took_local"] = sum(1 for v in choice.values() if v == "B")
        return apply(ta, tb, diff, choice)
