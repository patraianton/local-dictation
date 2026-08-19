# -*- coding: utf-8 -*-
"""Причёсывание текста локальной моделью через LM Studio.

Правило номер один: если что-то пошло не так — молча отдаём сырое распознавание.
Диктовка не должна ломаться из-за второй ступени.
"""
import difflib
import re
import time

import httpx

from . import endings

# Разбивка на слова с сохранением всего, что между ними (пробелы, знаки).
SPLIT_RE = re.compile(r"([^\W_]+)", re.UNICODE)
SENT_END = re.compile(r"[.!?…]['\"»)\s]*$")
CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE | re.UNICODE)

# Грубая транслитерация — нужна только чтобы сравнить, похоже ли услышанное
# кириллицей на термин латиницей.
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
    """Похоже ли услышанное на термин по звучанию.

    «акме паса» -> «AcmePass» похоже, «Mailwing» -> «Mailflow» нет.
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
    # Модели без режима размышления просто переписывают служебное слово в ответ.
    return NOTHINK_RE.sub("", text).strip()


def _strip_wrapping(text: str) -> str:
    text = text.strip()
    if len(text) > 1 and text[0] in '"«“\'' and text[-1] in '"»”\'':
        text = text[1:-1].strip()
    return text


def constrain(
    raw: str, polished: str, allowed: set[str], protected: set[str] | None = None
) -> str:
    """Замок на корректора.

    Берём от него только знаки препинания, заглавные буквы и подмену слов на
    термины из словаря. Всё остальное — свои слова, чужие выражения, «улучшенная»
    грамматика — откатываем к тому, что было сказано.

    allowed — слова, на которые МОЖНО заменять (термины из glossary.txt и
    правые части fixes.tsv), в нижнем регистре.
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
        """Можно ли принять эту подмену от корректора."""
        # Отдельно разрешаем одно: вернуть глаголу форму приказа.
        # «Сделаю session handover» -> «Сделай session handover». Только это,
        # только одно слово на одно и только по списку известных пар.
        if (
            said_words
            and len(words) == 1
            and len(said_words) == 1
            and endings.flip_allowed(said_words[0], words[0])
        ):
            return True
        if not words or not all(w.lower() in allowed for w in words):
            return False
        if said_words is None:  # дописал с нуля — только если это термин
            return True
        said = " ".join(said_words)
        # Подменять разрешаем только то, что распознавалка записала кириллицей
        # на слух. Иначе корректор меняет один термин из словаря на другой
        # («Mailwing» -> «Mailflow») — этого он делать не должен.
        if not CYRILLIC_RE.search(said):
            return False
        # Слова, которые он реально говорит по-русски, не переводим:
        # «сессию» остаётся «сессию», а не становится «session».
        if protected and any(w.lower() in protected for w in said_words):
            return False
        return sounds_like(said, " ".join(words))

    out: list[str] = []

    def put(
        word: str, j: int, said: str | None = None, raw_sep: str | None = None
    ) -> None:
        """Кладём слово, разделитель берём из причёсанного текста.

        said — как это слово прозвучало. Если корректор поменял только регистр
        и это не начало предложения и не термин — оставляем как было сказано.
        raw_sep — разделитель из сырого текста; берём его, если там был знак
        (точка в «github.exe» иначе теряется, когда слово возвращаем обратно).
        """
        if not out:
            sep = pol_parts[0]
            out.append(sep)
        else:
            # Разделитель есть только ПЕРЕД существующим словом. Если j уехал
            # за последнее слово, pol_parts[idx] — это хвост фразы (точка), и
            # ставить его как разделитель нельзя: выйдет «отчёты.делать».
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

    # «ё» против «е» — это не другое слово, а другое написание. Если считать
    # это заменой, разница склеивает соседние слова в один кусок и настоящая
    # подмена термина рядом с ней перестаёт проходить.
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
            # Корректор выкинул слово — возвращаем: это его речь, не наша.
            for k, i in enumerate(range(i1, i2)):
                put(raw_words[i], j1 + k, raw_sep=raw_parts[2 * i])
        elif tag == "insert":
            # Дописал от себя — берём, только если это термин из словаря.
            if ok(pol_words[j1:j2]):
                for j in range(j1, j2):
                    put(pol_words[j], j)

    out.append(pol_parts[-1] if len(pol_parts) > 1 else "")
    text = re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()
    return no_false_question(raw, text)


def flip_question(text: str) -> tuple[str, int, str]:
    """Меняет знак в конце: точка <-> вопрос.

    Отдаёт: новый текст, сколько знаков стереть в уже вставленном тексте,
    что вместо них набрать.

    Нужно потому, что «Скоро это уже закончится» и «Скоро это уже закончится?» —
    одни и те же слова: отличить их можно только по голосу, а по голосу вопрос
    не ловится (замер 14.08.2026 на 347 записях — 2 пойманных из 117).
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
    """Снимает «?» там, где вопроса быть не может или где он ничем не подкреплён.

    Три причины снять знак:

    1. Это поручение: «Ты мне скажи, какие журналы ты читал?» — глагол-приказ
       стоит раньше вопросительного слова. Знак вопроса на поручении заставляет
       агента переспрашивать вместо того, чтобы делать.
    2. Это хвост прошлой мысли: «Чтобы я мог спотчекнуть, как они выглядят?».
    3. Знак придумал корректор, а зацепиться не за что: распознавалка вопроса
       не услышала И вопросительного слова в тексте нет.

    Третье правило — самое ценное. Замер на 93 репликах с чужой разметкой
    (эталон ElevenLabs, 43 вопроса), 15.08.2026:

        только распознавалка     33 из 43, лишних 1  (точность 97%)
        корректор как есть       41 из 43, лишних 7  (точность 85%)
        + правила 1 и 2          37 из 43, лишних 3  (точность 92%)
        + правило 3             *36 из 43, лишних 1  (точность 97%)

    То есть третье правило стоит одного пойманного вопроса и убирает два лишних
    знака. Выбрано осознанно: лишний знак Антон замечает и злится, пропущенный —
    почти нет. Собственному знаку распознавалки верим: он редко ошибается.
    """
    if "?" not in text:
        return text
    # Знак от распознавалки смотрим по всей реплике: корректор может слепить или
    # разбить предложения, и сопоставить их по одному надёжно не выйдет.
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
    """Слова, на которые корректору МОЖНО подменять сказанное."""
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
        self.reason = "не проверялось"
        self._next_check = 0.0  # не долбимся в мёртвый LM Studio на каждой фразе
        self._client = httpx.Client(timeout=self.timeout)

    @property
    def system_prompt(self) -> str:
        base = SYSTEM_CLEAN if self.mode == "clean" else SYSTEM_LIGHT
        if not self.terms:
            return base
        return base + "\n\nСписок названий и терминов:\n" + ", ".join(self.terms[:150])

    def check(self, force: bool = False) -> bool:
        """Есть ли живой LM Studio с загруженной моделью."""
        if not self.enabled:
            self.available, self.reason = False, "выключено в настройках"
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
                self.reason = "в LM Studio нет загруженной чат-модели"
                return False
            if not self.model or self.model not in chat_ids:
                self.model = chat_ids[0]
            self.available, self.reason = True, "ок"
            self._next_check = 0.0
            return True
        except Exception as exc:
            self.available = False
            self.reason = f"LM Studio не отвечает ({type(exc).__name__})"
            return False

    def list_models(self) -> tuple[list[str], str]:
        """(какие модели видит LM Studio, почему не видит ничего).

        Нужно панели: человек выбирает корректора из того, что у него загружено,
        не лазая в config.toml.
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
        """Переключает корректора на другую модель прямо на ходу."""
        available, _why = self.list_models()
        if name not in available:
            return False
        self.model = name
        self.available = True
        self.reason = "ok"
        self._next_check = 0.0
        return True

    def ask(self, prompt: str, max_tokens: int = 8, timeout: float = 6.0) -> str:
        """Короткий вопрос модели с коротким ответом. Для одного решения."""
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
        """Гоняем пустышку, чтобы LM Studio подняла модель в видеопамять заранее.

        Без этого ПЕРВАЯ диктовка ждёт загрузку модели — на замере вышло 3 секунды
        против обычных 0,3.
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
        """(текст, секунды, что произошло). При любой заминке возвращает raw."""
        if not raw.strip():
            return raw, 0.0, "пусто"
        if not self.enabled:
            return raw, 0.0, "выключено"
        # На коротких фразах корректор ничего полезного не добавляет, а понести
        # его может: на «Проверь ещё раз» он выдал ответ в десять раз длиннее.
        if len(raw.split()) < self.min_words:
            return raw, 0.0, "коротко, без корректора"
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
            # Правильный способ отключить «размышления вслух» у моделей, которые
            # это умеют. Те, что не умеют, поле просто игнорируют.
            "chat_template_kwargs": {"enable_thinking": False},
            # Просим LM Studio не выгружать модель из видеопамяти сразу:
            # иначе первая диктовка после перерыва ждёт загрузку 2 секунды.
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
            return raw, time.perf_counter() - t0, f"сбой: {self.reason}"

        took = time.perf_counter() - t0
        text = _strip_wrapping(_strip_think(out))
        if not text:
            return raw, took, "модель вернула пустоту"
        ratio = len(text) / max(1, len(raw))
        if ratio > self.max_growth or ratio < 1.0 / self.max_growth:
            return raw, took, f"модель поехала (длина x{ratio:.2f})"
        if self.mode != "clean":
            # Замок: оставляем от корректора только знаки, заглавные и термины.
            fixed = constrain(raw, text, self.allowed, self.protected)
            note = "ок" if fixed == text else "ок, лишнее откатил"
            return fixed, took, note
        return text, took, "ок"
