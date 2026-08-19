# -*- coding: utf-8 -*-
"""An order versus a promise: "do it" and "I will do it".

Why this matters. The speaker dictates orders to agents, and the recognizer
hears the imperative ending as a first-person one, inverting the meaning:
"you do it" becomes "I will do it", and the agent answers "great, go ahead".
Measured on 377 takes: 6 such cases.

Why a rule does NOT fix it. Checked on the same 377 takes: a blind swap driven
by a verb list fired 10 times, 5 of them wrong — "I will go and see how it is"
turned into "go and see". Telling an order from a person talking about
themselves needs the meaning of the whole sentence, not the ending of one word.

What actually works, measured on 383 takes:

1. THE HINT TO THE RECOGNIZER, built from imperative verbs (asr.py, the
   "commands" style). The main lever: 4 lost orders -> 2. It works because at
   that point there is still audio, and in the audio the two endings really do
   differ.
2. THE CORRECTOR may flip a verb form, but only within the PAIRS list below:
   the lock in polish.py lets that single substitution through and nothing
   else. Measured: fixes 2 more cases out of 5 and never breaks the ones where
   the speaker really is talking about themselves.
3. You: Shift+F13, or an edit on the page — the pair is then remembered forever.

What does NOT work, and why (so nobody tries again):
- The blind verb-list rule (apply() below): 10 hits, 5 of them wrong.
  "I will go and see how it is" -> "go and see". Disabled in the settings.
- Asking the model directly, "order or about themselves?" (decide() below): the
  model cannot hear the audio and simply follows the phrasing of the question.
  With a leading question it scored 5/5 on orders and 2/5 on self-statements;
  with a neutral one, exactly the reverse. Not used.
"""
import re
from functools import lru_cache

# Pairs "as heard" -> "as meant". Left side is first person ("I will do"),
# right side is the imperative ("do"). Safe to extend.
PAIRS = {
    # imperatives ending in -й
    "сделаю": "сделай",
    "делаю": "делай",
    "запускаю": "запускай",
    "продолжаю": "продолжай",
    "отправляю": "отправляй",
    "начинаю": "начинай",
    "проверяю": "проверяй",
    "накидаю": "накидай",
    "переделаю": "переделай",
    "спланирую": "спланируй",
    "сформирую": "сформируй",
    "организую": "организуй",
    "использую": "используй",
    "реализую": "реализуй",
    "распакую": "распакуй",
    "верифицирую": "верифицируй",
    "оркестрирую": "оркестрируй",
    "паркую": "паркуй",
    "запаркую": "запаркуй",
    "отгружаю": "отгружай",
    "synkаю": "синкай",
    "синкаю": "синкай",
    # imperatives ending in -ь
    "проверю": "проверь",
    "поправлю": "поправь",
    "поставлю": "поставь",
    "отправлю": "отправь",
    "добавлю": "добавь",
    "готовлю": "готовь",
    # imperatives ending in -и
    "посмотрю": "посмотри",
    "покажу": "покажи",
    "скажу": "скажи",
    "напишу": "напиши",
    "найду": "найди",
    "соберу": "собери",
    "сохраню": "сохрани",
    "объясню": "объясни",
    "обновлю": "обнови",
    "подниму": "подними",
    "уберу": "убери",
    "остановлю": "останови",
    "удалю": "удали",
    "открою": "открой",
    "закрою": "закрой",
    "восстановлю": "восстанови",
    "пересоберу": "пересобери",
    "сравню": "сравни",
    "посчитаю": "посчитай",
    "перезапущу": "перезапусти",
    "запущу": "запусти",
    "пришлю": "пришли",
    "приложу": "приложи",
    "заведу": "заведи",
    "вынесу": "вынеси",
    "перенесу": "перенеси",
}

# If "я" or "мы" ("I"/"we") stands nearby, it really is about the speaker,
# not an order. Leave it alone.
SELF = {"я", "мы"}
# Split into sentences: "я" only protects the sentence it appears in.
SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
TOKEN = re.compile(r"([^\W_]+)", re.UNICODE)


def _fix_sentence(sentence: str) -> tuple[str, list[tuple[str, str]]]:
    parts = TOKEN.split(sentence)
    words = parts[1::2]
    changed: list[tuple[str, str]] = []
    said_self = False
    for k, word in enumerate(words):
        low = word.lower()
        if low in SELF:
            said_self = True
            continue
        if said_self or low not in PAIRS:
            continue
        fixed = PAIRS[low]
        if word[:1].isupper():
            fixed = fixed[:1].upper() + fixed[1:]
        parts[2 * k + 1] = fixed
        changed.append((word, fixed))
    return "".join(parts), changed


# The question is deliberately not slanted either way: a leading phrasing
# ("he hands out orders all day long") scored 5/5 on orders but broke 3 out of
# 5 self-statements. The model was simply agreeing with the prompt.
QUESTION = """Фраза, надиктованная голосом:
«{sentence}»

Слово «{word}» могло быть расслышано неверно: «{imperative}» и «{word}»
на слух почти одинаковы.

Какое из двух слов стоит в этой фразе по смыслу?

{imperative} — если человек велит сделать это кому-то другому
{word} — если человек говорит о своём собственном действии

Ответь одним словом — только само слово, без пояснений."""


def candidates(text: str) -> list[tuple[str, str, str]]:
    """Ambiguous verbs in a phrase: (word, imperative form, its sentence)."""
    out = []
    for sentence in SENT_SPLIT.split(text or ""):
        words = TOKEN.split(sentence)[1::2]
        if any(w.lower() in SELF for w in words):
            continue  # said "I" or "we": no need to ask
        for word in words:
            imp = PAIRS.get(word.lower())
            if imp:
                out.append((word, imp, sentence.strip()))
    return out


def decide(text: str, ask) -> tuple[str, list[tuple[str, str]]]:
    """Asks the model about each ambiguous verb and fixes orders only.

    ask(prompt) -> str. Any failure or unclear answer means: leave it alone.
    """
    changed: list[tuple[str, str]] = []
    result = text
    for word, imp, sentence in candidates(text):
        try:
            answer = (ask(QUESTION.format(
                sentence=sentence, word=word, imperative=imp)) or "").strip()
        except Exception:
            continue
        # One word expected. Anything but the imperative form is left alone.
        first = re.sub(r"[^\w]", "", answer.split()[0] if answer.split() else "").lower()
        if first != imp.lower():
            continue
        fixed = imp[:1].upper() + imp[1:] if word[:1].isupper() else imp
        new = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", fixed, result, count=1)
        if new != result:
            result = new
            changed.append((word, fixed))
    return result, changed


# Whole imperative forms, including those with no counterpart in PAIRS.
# Used to stop the corrector from sticking a "?" on an order.
IMPERATIVES = set(PAIRS.values()) | {
    "дай", "иди", "смотри", "слушай", "чини", "бери", "пиши", "ставь", "жги",
    "давай", "начни", "хватит", "прекрати", "сотри", "перепиши", "перезапусти",
    "верни", "убери", "пришли", "скинь", "залей", "выкати", "запарькуй",
    "запаркуй", "паркуй", "забей", "объедини", "раздели", "посчитай", "заведи",
}


# Question words. Forms with "-то" and "-нибудь" are excluded: "какую-то
# сводку" ("some summary or other") is not a question.
ASK_RE = re.compile(
    r"(?<!\w)(что|чего|чем|почему|зачем|сколько|где|когда|куда|откуда|кто|кого|"
    r"кому|как|какой|какая|какое|какие|какую|каком|каким|чей|ли)"
    r"(?!-(?:то|нибудь))(?!\w)",
    re.IGNORECASE | re.UNICODE,
)


# Morphological analysis against a Russian dictionary. Needed to recognize an
# imperative by its form rather than from a list: a hand-written list will
# always have holes — that is exactly why the corrector stuck a question mark on
# "Переводи их на ажур" on 2026-08-14.
# If the library is missing, fall back to the list as before. Dictation still
# works either way.
try:
    import pymorphy3

    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # pragma: no cover — на машине без библиотеки
    _MORPH = None


@lru_cache(maxsize=4096)
def is_imperative(word: str) -> bool:
    """Is this word an imperative.

    The list first: it holds words the dictionary does not know ("синкай") and
    words it reads differently ("удали" is also a form of the noun "удаль").
    Then the morphology, which closes the holes in the list.

    Only the TOP parse counts. "Статьи", "доски", "деньги" all have an
    imperative parse somewhere down the list, and accepting any parse would make
    the rule eat real questions.
    """
    w = word.lower()
    if w in IMPERATIVES:
        return True
    if _MORPH is None:
        return False
    parses = _MORPH.parse(w)
    return bool(parses) and parses[0].tag.mood == "impr"


WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


@lru_cache(maxsize=4096)
def _impr_and_past(word: str) -> bool:
    """The word has BOTH an imperative parse AND a past-tense parse.

    "Пришли" is both "send me the file" and "the money arrived". There are few
    such words, but they are dangerous: the dictionary ranks the imperative
    first.
    """
    if _MORPH is None:
        return False
    parses = _MORPH.parse(word.lower())
    return any(p.tag.mood == "impr" for p in parses) and any(
        p.tag.tense == "past" for p in parses
    )


@lru_cache(maxsize=4096)
def _may_be_subject(word: str) -> bool:
    """Could this word be a subject: a noun or pronoun in the nominative."""
    if _MORPH is None:
        return False
    return any(
        ("NOUN" in p.tag or "NPRO" in p.tag) and "nomn" in p.tag
        for p in _MORPH.parse(word.lower())
    )


def _has_subject_before(sentence: str, pos: int) -> bool:
    """Is there a subject before this position, within the same clause.

    A clause starts after the last comma: in "Ключи пришли, пришли мне ещё"
    ("The keys arrived, send me more") the first clause has a subject, the
    second does not.
    """
    start = max(
        sentence.rfind(",", 0, pos),
        sentence.rfind(";", 0, pos),
        sentence.rfind("—", 0, pos),
    ) + 1
    return any(
        _may_be_subject(m.group(0))
        for m in WORD_RE.finditer(sentence[start:pos])
    )


def starts_with_command(sentence: str) -> bool:
    """An order, not a question — so a question mark here is wrong.

    The test: the imperative verb stands EARLIER than the question word.

        "Ты мне скажи, какие журналы ты читал"  — an order (скажи < какие)
        "Какие журналы ты читал?"               — a question (no imperative)

    Why word order rather than the first word. An order can sit anywhere: at the
    start ("Переводи их на ажур"), after a filler ("Хорошо, ставь CRM
    мониторить"), or at the end ("У нас есть ролик про Bitrix, найди его").
    A question word inside an order is not a question but a statement of WHAT
    exactly to do: "объясни мне, когда будет результат".

    The price, chosen deliberately by the owner on 2026-08-14: the mark is also
    removed from polite requests such as "Посчитай, сколько их у нас?" — 28 out
    of 327 sentences carrying a question mark. Reason: a question mark on an
    order makes an agent ask back instead of doing the work.
    """
    if not sentence:
        return False
    ask = ASK_RE.search(sentence)
    ask_pos = ask.start() if ask else None
    for m in WORD_RE.finditer(sentence):
        word = m.group(0)
        if is_imperative(word):
            # An imperative never has a subject before it in the same
            # clause: "Деньги пришли?", "Все ключи уже пришли?" are past tense,
            # not "send me the keys". Only ambiguous words are checked;
            # otherwise "Ещё раз объясни мне" would hide behind "раз", and
            # "Ты мне скажи" behind "ты".
            if _impr_and_past(word) and _has_subject_before(sentence, m.start()):
                continue
            return ask_pos is None or m.start() < ask_pos
        if ask_pos is not None and m.start() > ask_pos:
            break  # question word before any imperative: it is a question
    return False


# Conjunctions that start not a separate thought but the tail of the previous
# one: "Сделай мне CSV. Чтобы я мог спотчекнуть, как они выглядят."
# Such a sentence cannot be a question, and a mark on it is a corrector error
# (a real case from 2026-08-14, marked bad by the owner). There is almost always
# a question word inside ("как", "что"), so the ASK_RE test used for orders is
# not enough here.
SUBORDINATE = {"чтобы", "чтоб"}

# The short retort "Чтобы что?" ("So that what?") is a real question. Length
# tells them apart: the tail of a previous thought is always longer.
SUBORDINATE_MIN_WORDS = 4


def starts_with_subordinate(sentence: str) -> bool:
    """A subordinate clause: a continuation of the previous thought, not a question."""
    words = TOKEN.split(sentence or "")[1::2]
    if len(words) < SUBORDINATE_MIN_WORDS:
        return False
    return words[0].lower() in SUBORDINATE


def flip_allowed(said: str, other: str) -> bool:
    """May the corrector make this substitution: are these two forms of one verb?

    Only "сделаю" <-> "сделай" and the like, from the PAIRS list, in either
    direction. The lock rolls back everything else.
    """
    a, b = said.lower(), other.lower()
    return PAIRS.get(a) == b or PAIRS.get(b) == a


def apply(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(corrected text, what exactly was changed)."""
    if not text:
        return text, []
    out, changed = [], []
    for sentence in SENT_SPLIT.split(text):
        fixed, ch = _fix_sentence(sentence)
        out.append(fixed)
        changed.extend(ch)
    # Разделители предложений съедаются split'ом — собираем через пробел,
    # знаки препинания остаются внутри самих предложений.
    return " ".join(out), changed
