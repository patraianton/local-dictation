# -*- coding: utf-8 -*-
"""Tidying the text with a local model through LM Studio.

Rule number one: if anything goes wrong, quietly return the raw recognition.
Dictation must never break because of the second stage.

The system prompts below are deliberately written in Russian: they instruct the
model about Russian text, and every measurement in this project was made with
them as they are. Translating them would invalidate all of it.
"""
import difflib
import re
import time

import httpx

from . import endings

# Split into words, keeping everything in between (spaces, punctuation).
SPLIT_RE = re.compile(r"([^\W_]+)", re.UNICODE)
SENT_END = re.compile(r"[.!?…]['\"»)\s]*$")
CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE | re.UNICODE)

# Rough transliteration, used only to compare whether something written in
# Cyrillic by ear sounds like a Latin-script term.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "u", "я": "a",
}


def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(c, c) for c in text.lower())


def sounds_like(said: str, term: str, threshold: float = 0.55) -> bool:
    """Does what was heard sound like the term.

    "акме паса" -> "AcmePass" is close enough; "Mailwing" -> "Mailflow" is not.
    """
    a = re.sub(r"\W", "", translit(said))
    b = re.sub(r"\W", "", term.lower())
    if not a or not b:
        return False
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio() >= threshold

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
OPEN_THINK_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)
NOTHINK_RE = re.compile(r"\s*/no_?think\s*$", re.IGNORECASE)

_HEAD = """Ты — корректор расшифровки устной речи. На вход даётся текст, который \
распознавалка услышала с микрофона. Говорящий — русскоязычный, в речи постоянно \
встречаются английские названия и рабочие термины."""

_TAIL = """
Строго запрещено:
- добавлять что-либо от себя, отвечать на текст, комментировать или объяснять его;
- переводить с русского на английский или обратно;
- менять смысл, порядок мыслей, стиль и лексику говорящего;
- пересказывать, сокращать или дополнять;
- смягчать, вычищать мат и грубость — это его речь, а не твоя.

Если текст непонятен или пуст — верни его без изменений.
В ответе — только исправленный текст. Без кавычек, без пояснений, без заголовков."""

# Осторожный режим: трогаем оформление и термины, слова оставляем как есть.
SYSTEM_LIGHT = _HEAD + """

Что делать (и больше ничего):
- расставить знаки препинания и заглавные буквы;
- правильно написать названия и термины из списка ниже, даже если распознавалка \
передала их кириллицей на слух («кложд код» -> «Claude Code», «луп» -> «loop»);
- починить явно расслышанное неверно, когда из фразы понятно, что имелось в виду \
(«два мои рецепта» -> «два моих рецепта»);
- вернуть вопросительный знак там, где по смыслу задан вопрос: распознавалка \
часто ставит точку вместо «?», потому что в устной речи вопрос слышен только по \
интонации («Мы заливаем статьи уже.» -> «Мы заливаем статьи уже?»). Ставь «?» \
только когда это правда вопрос, а не на всякий случай. Поручение вопросом не \
бывает: «Восстанови все прогоны», «Сохрани ключ», «Сделай отчёт» — это приказы, \
знак вопроса на них не ставится никогда;
- ГЛАВНОЕ: вернуть глаголу верное лицо. Говорящий диктует поручения, а \
распознавалка часто слышит приказ как рассказ о себе: «сделай» превращается в \
«сделаю», «отправляй» в «отправляю», «продолжай» в «продолжаю». Если по смыслу \
это поручение — верни повелительную форму. Если человек правда говорит о себе \
(«я схожу и посмотрю, что там как») — НЕ трогай.
""" + _TAIL

# Чистый режим: дополнительно убираем мусор устной речи.
SYSTEM_CLEAN = _HEAD + """

Что делать:
- расставить знаки препинания и заглавные буквы;
- правильно написать названия и термины из списка ниже, даже если распознавалка \
передала их кириллицей на слух («кложд код» -> «Claude Code», «луп» -> «loop»);
- починить явно расслышанное неверно;
- убрать слова-паразиты («э», «ну», «как бы», «это самое»), повторы одного и того \
же слова подряд и оговорки-самоисправления — оставить то, что человек в итоге сказал.
""" + _TAIL

SYSTEM = SYSTEM_LIGHT  # умолчание, если режим не задан


def _strip_think(text: str) -> str:
    text = THINK_RE.sub("", text)
    if "</think>" in text.lower():
        text = OPEN_THINK_RE.sub("", text)
    # Models without a thinking mode simply echo the control word back.
    return NOTHINK_RE.sub("", text).strip()


def _strip_wrapping(text: str) -> str:
    text = text.strip()
    if len(text) > 1 and text[0] in '"«“\'' and text[-1] in '"»”\'':
        text = text[1:-1].strip()
    return text


def constrain(
    raw: str, polished: str, allowed: set[str], protected: set[str] | None = None
) -> str:
    """The lock on the corrector.

    Only three things are taken from it: punctuation, capital letters and
    replacing a word with a glossary term. Everything else — reworded phrases,
    somebody else's expressions, "improved" grammar — is rolled back to what was
    actually said.

    allowed — the words it MAY substitute in (glossary.txt terms and the right
    column of fixes.tsv), lowercase.
    """
    raw_parts = SPLIT_RE.split(raw)
    pol_parts = SPLIT_RE.split(polished)
    raw_words = raw_parts[1::2]
    pol_words = pol_parts[1::2]
    if not raw_words:
        return raw
    if not pol_words:
        return raw

    def ok(words: list[str], said_words: list[str] | None = None) -> bool:
        """May this substitution from the corrector be accepted."""
        # One thing is allowed on its own: restoring the imperative form of
        # a verb. "Сделаю session handover" -> "Сделай session handover". Only
        # that, only one word for one, and only from the known pair list.
        if (
            said_words
            and len(words) == 1
            and len(said_words) == 1
            and endings.flip_allowed(said_words[0], words[0])
        ):
            return True
        if not words or not all(w.lower() in allowed for w in words):
            return False
        if said_words is None:  # added from nothing: only if it is a term
            return True
        said = " ".join(said_words)
        # Substitution is allowed only for what the recognizer wrote in
        # Cyrillic by ear. Otherwise the corrector swaps one glossary term for
        # another ("Mailwing" -> "Mailflow"), which it must never do.
        if not CYRILLIC_RE.search(said):
            return False
        # Words the speaker genuinely says in their own language are not
        # translated: "сессию" stays "сессию" and never becomes "session".
        if protected and any(w.lower() in protected for w in said_words):
            return False
        return sounds_like(said, " ".join(words))

    out: list[str] = []

    def put(
        word: str, j: int, said: str | None = None, raw_sep: str | None = None
    ) -> None:
        """Emit a word; the separator comes from the corrected text.

        said — how the word was actually spoken. If the corrector changed only
        the case, and this is neither a sentence start nor a term, keep it as
        spoken.
        raw_sep — the separator from the raw text; used when it carried
        punctuation (the dot in "github.exe" is otherwise lost when the word is
        put back).
        """
        if not out:
            sep = pol_parts[0]
            out.append(sep)
        else:
            # A separator exists only BEFORE an existing word. If j ran past
            # the last word, pol_parts[idx] is the tail of the phrase (a full
            # stop) and must not be used as a separator: the result would be
            # "отчёты.делать".
            idx = 2 * j
            sep = pol_parts[idx] if 0 < idx < 2 * len(pol_words) else " "
            if raw_sep and raw_sep.strip():
                sep = raw_sep
            sep = sep or " "
            out.append(sep)
        if (
            said is not None
            and word != said
            and word.lower().replace("ё", "е") == said.lower().replace("ё", "е")
            and word[:1].isupper()
            and said[:1].islower()
            and word.lower() not in allowed
            and len(out) > 2
            and not SENT_END.search(sep)
        ):
            word = said
        out.append(word)

    # "ё" versus "е" is a different spelling, not a different word. Treating
    # it as a substitution glues neighbouring words into one chunk, and a real
    # term substitution next to it stops going through.
    def key(w: str) -> str:
        return w.lower().replace("ё", "е")

    sm = difflib.SequenceMatcher(
        a=[key(w) for w in raw_words], b=[key(w) for w in pol_words],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for j in range(j1, j2):
                put(pol_words[j], j, raw_words[i1 + (j - j1)])
        elif tag == "replace":
            if ok(pol_words[j1:j2], raw_words[i1:i2]):
                for j in range(j1, j2):
                    put(pol_words[j], j)
            else:
                for k, i in enumerate(range(i1, i2)):
                    put(raw_words[i], j1 + k, raw_sep=raw_parts[2 * i])
        elif tag == "delete":
            # The corrector dropped a word: put it back. It is the speaker's
            # speech, not ours.
            for k, i in enumerate(range(i1, i2)):
                put(raw_words[i], j1 + k, raw_sep=raw_parts[2 * i])
        elif tag == "insert":
            # Added on its own initiative: accept only if it is a glossary term.
            if ok(pol_words[j1:j2]):
                for j in range(j1, j2):
                    put(pol_words[j], j)

    out.append(pol_parts[-1] if len(pol_parts) > 1 else "")
    text = re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()
    return no_false_question(raw, text)


def flip_question(text: str) -> tuple[str, int, str]:
    """Flips the final mark: full stop <-> question mark.

    Returns: the new text, how many characters to erase in the already-pasted
    text, and what to type instead.

    Needed because "Скоро это уже закончится" and "Скоро это уже закончится?"
    are the same words: only the voice tells them apart, and the voice does not
    carry the signal (measured 2026-08-14 on 347 takes — 2 caught out of 117).
    """
    tail = text.rstrip()
    trail = text[len(tail):]
    if not tail:
        return text, 0, ""
    if tail.endswith("?"):
        return tail[:-1] + "." + trail, 1, "."
    if tail.endswith("..."):
        return tail[:-3] + "?" + trail, 3, "?"
    if tail.endswith(("…", ".", "!")):
        return tail[:-1] + "?" + trail, 1, "?"
    return tail + "?" + trail, 0, "?"


SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]*")


def no_false_question(raw: str, text: str) -> str:
    """Removes a "?" where a question is impossible or simply unsupported.

    Three reasons to drop the mark:

    1. It is an order: "Ты мне скажи, какие журналы ты читал?" — the imperative
       verb stands earlier than the question word. A question mark on an order
       makes an agent ask back instead of doing the work.
    2. It is the tail of a previous thought: "Чтобы я мог спотчекнуть, как они
       выглядят?".
    3. The corrector invented the mark with nothing to go on: the recognizer did
       not hear a question AND there is no question word in the text.

    The third rule is the valuable one. Measured on 93 single-sentence takes
    against an independent reference (ElevenLabs, 43 questions), 2026-08-15:

        recognizer alone        33 of 43, 1 false  (precision 97%)
        corrector unrestricted  41 of 43, 7 false  (precision 85%)
        + rules 1 and 2         37 of 43, 3 false  (precision 92%)
        + rule 3               *36 of 43, 1 false  (precision 97%)

    So the third rule costs one caught question and removes two false marks.
    Chosen deliberately: a false mark is noticed and resented, a missing one
    almost never is. The recognizer's own mark is trusted: it rarely errs.
    """
    if "?" not in text:
        return text
    # The recognizer's mark is checked across the whole take: the corrector may
    # merge or split sentences, so matching them one to one is not reliable.
    heard_question = "?" in (raw or "")
    out = []
    for sentence in SENTENCE_RE.findall(text):
        stripped = sentence.rstrip()
        if stripped.endswith("?") and (
            endings.starts_with_command(sentence)
            or endings.starts_with_subordinate(sentence)
            or not (heard_question or endings.ASK_RE.search(sentence))
        ):
            sentence = sentence.replace("?", ".", 1)
        out.append(sentence)
    return "".join(out) if out else text


def allowed_words(terms: list[str], fixes=None) -> set[str]:
    """Words the corrector MAY substitute for what was said."""
    out: set[str] = set()
    for term in terms:
        out.update(w.lower() for w in SPLIT_RE.findall(term))
    if fixes is not None:
        for dst, _hits, _origin in getattr(fixes, "pairs", {}).values():
            out.update(w.lower() for w in SPLIT_RE.findall(dst))
    return out


class Polisher:
    def __init__(self, cfg: dict, terms: list[str], fixes=None, protected=None):
        p = cfg.get("polish", {})
        self.enabled = bool(p.get("enabled", True))
        self.base = p.get("url", "http://127.0.0.1:1234").rstrip("/")
        self.model = p.get("model", "") or ""
        self.timeout = float(p.get("timeout_s", 4.0))
        self.max_growth = float(p.get("max_growth", 1.6))
        self.mode = p.get("mode", "light")
        self.min_words = int(p.get("min_words", 4))
        self.keep_loaded_s = int(p.get("keep_loaded_s", 3600))
        self.terms = terms
        self.allowed = allowed_words(terms, fixes)
        self.protected = protected or set()
        self.available = False
        self.reason = "not checked yet"
        self._next_check = 0.0  # do not hammer a dead LM Studio on every take
        self._client = httpx.Client(timeout=self.timeout)

    @property
    def system_prompt(self) -> str:
        base = SYSTEM_CLEAN if self.mode == "clean" else SYSTEM_LIGHT
        if not self.terms:
            return base
        return base + "\n\nСписок названий и терминов:\n" + ", ".join(self.terms[:150])

    def check(self, force: bool = False) -> bool:
        """Is there a live LM Studio with a model loaded."""
        if not self.enabled:
            self.available, self.reason = False, "disabled in the settings"
            return False
        if not force and time.time() < self._next_check:
            return False
        self._next_check = time.time() + 30.0
        try:
            r = self._client.get(f"{self.base}/v1/models", timeout=2.0)
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            chat_ids = [i for i in ids if "embed" not in i.lower()]
            if not chat_ids:
                self.available = False
                self.reason = "LM Studio has no chat model loaded"
                return False
            if not self.model or self.model not in chat_ids:
                self.model = chat_ids[0]
            self.available, self.reason = True, "ok"
            self._next_check = 0.0
            return True
        except Exception as exc:
            self.available = False
            self.reason = f"LM Studio is not answering ({type(exc).__name__})"
            return False

    def list_models(self) -> tuple[list[str], str]:
        """(models LM Studio can see, or why it sees none).

        Used by the page: the user picks a corrector from what is actually
        loaded, without editing config.toml.
        """
        try:
            r = self._client.get(f"{self.base}/v1/models", timeout=2.0)
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            chat = [i for i in ids if i and "embed" not in i.lower()]
            if not chat:
                return [], "LM Studio is running but no chat model is loaded"
            return sorted(chat), ""
        except Exception as exc:
            return [], f"LM Studio is not answering ({type(exc).__name__})"

    def use_model(self, name: str) -> bool:
        """Switches the corrector to another model on the fly."""
        available, _why = self.list_models()
        if name not in available:
            return False
        self.model = name
        self.available = True
        self.reason = "ok"
        self._next_check = 0.0
        return True

    def ask(self, prompt: str, max_tokens: int = 8, timeout: float = 6.0) -> str:
        """A short question to the model with a short answer. One decision."""
        r = self._client.post(
            f"{self.base}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
                "ttl": self.keep_loaded_s,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return _strip_think(r.json()["choices"][0]["message"]["content"])

    def warmup(self) -> float:
        """Sends a dummy request so LM Studio loads the model into VRAM early.

        Without it the FIRST take waits for the model to load — measured at 3
        seconds against the usual 0.3.
        """
        if not self.available:
            return 0.0
        t0 = time.perf_counter()
        try:
            self._client.post(
                f"{self.base}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ок"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": False,
                    "ttl": self.keep_loaded_s,
                },
                timeout=180.0,
            )
        except Exception:
            pass
        return time.perf_counter() - t0

    def polish(self, raw: str) -> tuple[str, float, str]:
        """(text, seconds, what happened). Returns raw on any hiccup."""
        if not raw.strip():
            return raw, 0.0, "empty"
        if not self.enabled:
            return raw, 0.0, "disabled"
        # On short phrases the corrector used to be skipped entirely — see
        # config.toml, [polish] min_words, for why that turned out to be wrong.
        if len(raw.split()) < self.min_words:
            return raw, 0.0, "too short, skipped"
        if not self.available and not self.check():
            return raw, 0.0, self.reason

        t0 = time.perf_counter()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": raw},
            ],
            "temperature": 0.0,
            "max_tokens": min(1200, int(len(raw) / 2) + 100),
            "stream": False,
            # The proper way to switch off "thinking out loud" in models that
            # support it. Models that do not simply ignore the field.
            "chat_template_kwargs": {"enable_thinking": False},
            # Ask LM Studio not to unload the model from VRAM immediately:
            # otherwise the first take after a pause waits 2 seconds for it.
            "ttl": self.keep_loaded_s,
        }
        try:
            r = self._client.post(
                f"{self.base}/v1/chat/completions", json=body, timeout=self.timeout
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            self.available = False
            self.reason = f"{type(exc).__name__}"
            return raw, time.perf_counter() - t0, f"failed: {self.reason}"

        took = time.perf_counter() - t0
        text = _strip_wrapping(_strip_think(out))
        if not text:
            return raw, took, "the model returned nothing"
        ratio = len(text) / max(1, len(raw))
        if ratio > self.max_growth or ratio < 1.0 / self.max_growth:
            return raw, took, f"the model ran off (length x{ratio:.2f})"
        if self.mode != "clean":
            # The lock: keep only punctuation, capitals and terms.
            fixed = constrain(raw, text, self.allowed, self.protected)
            note = "ok" if fixed == text else "ok, rolled back extras"
            return fixed, took, note
        return text, took, "ok"
