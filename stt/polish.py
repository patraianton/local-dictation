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
import sys
import threading
import time

import pathlib
import httpx

from . import endings

# Split into words, keeping everything in between (spaces, punctuation).
SPLIT_RE = re.compile(r"([^\W_]+)", re.UNICODE)
SENT_END = re.compile(r"[.!?…]['\"»)\s]*$")
# Checked as a tuple, not a string: an empty string counts as "in" any
# string, and text[:1] is empty on an empty take.
DASHES = ("—", "–", "-")
DOUBLE_DASH_RE = re.compile(r"—\s*—")
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

    def ok(words: list[str], said_words: list[str]) -> bool:
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
            # raw_sep is passed only where the corrector's own separator cannot
            # be trusted: around a word we are rolling back. There the
            # punctuation belongs to a rewrite that is being thrown away, so
            # the raw one wins even when it is a plain space.
            if raw_sep is not None:
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
    # Set right after a word has been rolled back. The separator that follows
    # such a word is the corrector's, and it describes a phrase that no longer
    # exists — see the note in put().
    rolled_back = False

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for j in range(j1, j2):
                i = i1 + (j - j1)
                # Only the FIRST word after a rollback: from there on the two
                # texts agree again and the corrector's punctuation is welcome.
                after = raw_parts[2 * i] if (rolled_back and j == j1) else None
                put(pol_words[j], j, raw_words[i], raw_sep=after)
            rolled_back = False
        elif tag == "replace":
            if ok(pol_words[j1:j2], raw_words[i1:i2]):
                for j in range(j1, j2):
                    put(pol_words[j], j)
                rolled_back = False
            else:
                for k, i in enumerate(range(i1, i2)):
                    put(raw_words[i], j1 + k, raw_sep=raw_parts[2 * i])
                rolled_back = True
        elif tag == "delete":
            # The corrector dropped a word: put it back. It is the speaker's
            # speech, not ours.
            for k, i in enumerate(range(i1, i2)):
                put(raw_words[i], j1 + k, raw_sep=raw_parts[2 * i])
            rolled_back = True
        elif tag == "insert":
            # A word the speaker never said does not go in. Not even a glossary
            # term: the branch used to accept those, and measured over 2218
            # takes every single one of the six it let through was wrong. The
            # worst, on 2026-08-20: "Там Bitrix, GoHighLevel и так далее" was
            # pasted as "Там Bitrix, GoHighLevel, Salesforce, HubSpot,
            # Pipedrive, Zoho, и так далее" — four systems the owner never
            # named, written into a document he was dictating. A term the corrector
            # splits into two words ("акме пас" -> "Acme Pass") is not this
            # case: that is a replace, and it still goes through.
            #
            # An invented word usually brought a comma with it: "я новую сессию
            # начал" came back as "я, когда новую сессию начал". The word goes,
            # so its comma goes too.
            rolled_back = True

    out.append(pol_parts[-1] if len(pol_parts) > 1 else "")
    text = re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()
    # A dash opening the text is the corrector reading the take as a line
    # of dialogue: "Все дело, не надо было" came back as "— Всё дело — не
    # надо было" (22.08.2026). Dictation is never dialogue.
    if text[:1] in DASHES and raw.lstrip()[:1] not in DASHES:
        text = text[1:].lstrip()
    # And a dash doubled onto one the speaker had already said.
    text = DOUBLE_DASH_RE.sub("—", text)
    # The corrector is allowed to ADD a capital letter, and put() above already
    # rolls that back where it does not belong. The mirror case had no rule at
    # all: it could TAKE one away from the very first word, and the take then
    # started with a small letter — "Что вообще делать?" pasted as "что вообще
    # делать?" (29.08.2026, and twice more in the four days before). A take
    # always opens a sentence, so lowercasing its first word is never right.
    # Only the first word: further along, lowercasing is often a real fix for a
    # capital the recognizer put in the middle of a phrase.
    said_head = raw.lstrip()[:1]
    if said_head.isupper() and text[:1].islower() and text[:1] == said_head.lower():
        text = said_head + text[1:]
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

    Rule 4 (added 2026-08-20). Rule 3 turned out to be full of holes: one
    question word anywhere in the sentence — or one question mark heard anywhere
    in the take — let the corrector stick marks wherever it pleased. Three real
    failures in a single day, all of them on takes where the recognizer heard no
    question at that spot at all:

        heard "…а это где-то внизу закопано."   ->  "…внизу закопано?"
        heard "…когда страница рефрешена."      ->  "…рефрешена?"
        heard "…чтобы я в другой сессии тоже это мог запустить?"
                          ->  "…чтобы я в другой сессии? тоже это мог запустить?"

    Over the 227 takes since rule 3 landed the corrector added 7 marks; 5 of
    them were wrong. So the count is capped now: the result may not carry more
    question marks than the recognizer heard. Which of them survive is decided
    by how well a sentence matches a raw sentence that really ended with "?" —
    that is what keeps the mark on "…мог запустить?" and takes it off the
    invented split in the middle.

    The price is the questions the recognizer misses entirely: 33 of 43 instead
    of 36. The owner still has ctrl+f13 to put a mark back by hand. To go back
    to the old behaviour: config.toml -> [polish] questions = "corrector".

    Rule 5 (added 2026-08-22). Rules 1 and 2 used to fire even on a mark the
    recognizer had heard, so every question shaped as a request lost its mark:

        heard "Выясни, кто за ночь пытался авторизировать Linear?"
        heard "Скажи, ты читал какой-то handover документ?"
        heard "Посмотри, с чем может быть связано ухудшение качества диктовки?"

    All three came back as full stops. This reverses the 14.08 decision that
    "an order stays an order even with a heard mark". The reason it was made —
    a mark on an order makes an agent ask back instead of working — was about
    marks the corrector INVENTED on orders; it was never meant to overrule the
    voice. On 22.08 30% of the owner's heard questions opened with an
    imperative verb, against 0-8% on every earlier day, and the rule went from
    a rounding error to a third of his questions.

    So a mark the recognizer heard is now left alone, and cap_questions still
    keeps the count down to what was actually heard.
    """
    if "?" not in text:
        return text
    # The recognizer's mark is checked across the whole take: the corrector may
    # merge or split sentences, so matching them one to one is not reliable.
    heard_question = "?" in (raw or "")
    # Rules 1-3 exist to kill marks the CORRECTOR invented. When the recognizer
    # heard a question itself, they stand down: the voice is the ground truth
    # for whether a question was asked, and the count is capped further down by
    # cap_questions anyway. See rule 5 in the docstring for why this reverses
    # the 14.08 decision.
    guard = not heard_question or questions_mode == "corrector"
    out = []
    for sentence in SENTENCE_RE.findall(text):
        stripped = sentence.rstrip()
        if stripped.endswith("?") and guard and (
            endings.starts_with_command(sentence)
            or endings.starts_with_subordinate(sentence)
            or not (heard_question or endings.ASK_RE.search(sentence))
        ):
            sentence = sentence.replace("?", ".", 1)
        out.append(sentence)
    text = "".join(out) if out else text
    return text if questions_mode == "corrector" else cap_questions(raw, text)


# How question marks are decided. "heard" — never more than the recognizer
# heard; "corrector" — the old behaviour, the corrector may add its own.
questions_mode = "heard"


def _tail_key(sentence: str, words: int = 3) -> str:
    """The last few words of a sentence, lowercase — for matching raw to result."""
    found = SPLIT_RE.findall(sentence or "")
    return " ".join(w.lower() for w in found[-words:])


# A comma and "а"/"но"/"и" start a new clause: whatever the sentence began
# with, the mark at the end no longer belongs to it. This is the whole
# difference between "Почему статьи не залились?" — a real question the
# recognizer missed — and "Почему у меня три Айдала, а это где-то внизу
# закопано?", which is a complaint with a made-up mark.
CLAUSE_BREAK_RE = re.compile(r",\s*(?:а|но|и|да)\s", re.IGNORECASE | re.UNICODE)

# How far into the sentence the question word may stand. Six words is measured,
# not guessed: "Только опять я не понимаю, почему тебе нужен мой компьютер?" —
# a real restored question from 15.08 — carries five words before "почему".
# Seven would let back "Можно сделать как-то, чтобы не мелькали, когда страница
# рефрешена?", where "когда" is the eighth word and no question is being asked.
ASK_WITHIN_WORDS = 6

# "Что" and "как" are question words at the head of a sentence and ordinary
# conjunctions after a comma — and in this speaker's dictation the second is
# what they nearly always are: "вот тут написано, что у тебя memory missing",
# "разбирайся, что с ним не так" (an order). So after a comma these two alone
# do not open the loophole. Measured over the takes from 20.08.2026 on: 5 marks
# that should not have been there go, 3 real questions go with them. The owner
# chose that trade on 2026-08-25 — a wrong mark makes an agent ask back instead
# of working, a missing one is one Ctrl+F13 away.
#
# The test is the comma, not the word's position. "Чтобы что?" is a real short
# retort with "что" second, and a rule of "only the very first word" killed it.
SOFT_ASK = {"что", "как"}

# Words a person opens their mouth with before the thought starts: they carry
# no meaning of their own, and the comma after them is not a clause break.
# Until 10.09.2026 they were treated as one, and that alone killed a whole
# question three days running — the owner asked "Хорошо, как скоро мы это
# закончим?" three times in a row, each time got a full stop, and each time
# said it again. The corrector had it right ("Как скоро мы это закончим?"); the
# rollback of the dropped "Хорошо," put "как" after a comma, and the rule above
# then read it as a conjunction.
FILLERS = {
    "хорошо", "окей", "ок", "ладно", "так", "ну", "вот", "слушай", "слушайте",
    "смотри", "смотрите", "кстати", "короче", "давай", "давайте", "блядь",
    "блять", "да", "нет", "и", "а", "но", "значит", "погоди", "подожди",
}


def _only_fillers(prefix: str) -> bool:
    """Nothing but filler words stands before the question word."""
    words = [w.lower() for w in SPLIT_RE.findall(prefix)]
    return bool(words) and len(words) <= 3 and all(w in FILLERS for w in words)


def corrector_may_add(sentence: str) -> bool:
    """May the corrector put a mark the recognizer never heard on this sentence.

    Only when the sentence opens as a question: a question word among the first
    few words, and no new clause between it and the mark. Filler words in front
    do not count as a clause — see FILLERS.
    """
    ask = endings.ASK_RE.search(sentence or "")
    if not ask:
        return False
    prefix = sentence[: ask.start()]
    if (ask.group(1).lower() in SOFT_ASK and "," in prefix
            and not _only_fillers(prefix)):
        return False
    before = len(SPLIT_RE.findall(prefix))
    if before >= ASK_WITHIN_WORDS:
        return False
    return not CLAUSE_BREAK_RE.search(sentence[ask.end():])


def cap_questions(raw: str, text: str) -> str:
    """No more question marks than the recognizer actually heard.

    The marks the recognizer did hear are handed to the sentences that support
    them best: first the ones opening with a question word, then the ones ending
    the same way as a raw sentence that carried a mark, then left to right.

    The order matters. "Что у тебя есть, я тебе сейчас буду делать?" — the
    recognizer put its single mark at the very end of a run-on, and the corrector
    split it in two. By the tail the mark belongs to "…буду делать", but the
    question is the first half. The opening word knows better than the tail.

    Everything above that count is the corrector's own idea and survives only if
    the sentence opens as a question — see corrector_may_add.
    """
    heard = (raw or "").count("?")
    sentences = SENTENCE_RE.findall(text)
    marked = [i for i, s in enumerate(sentences) if s.rstrip().endswith("?")]
    if len(marked) <= heard:
        return text

    raw_tails = {
        _tail_key(s) for s in SENTENCE_RE.findall(raw or "") if s.rstrip().endswith("?")
    }

    def support(i: int) -> tuple:
        sentence = sentences[i]
        tail = _tail_key(sentence.rstrip().rstrip("?"))
        ask = endings.ASK_RE.search(sentence)
        first = SPLIT_RE.search(sentence)
        opens = bool(ask and first and ask.start() <= first.start())
        return (opens, tail in raw_tails and bool(tail), -i)

    keep = set(sorted(marked, key=support, reverse=True)[:heard])
    for i in marked:
        if i not in keep and not corrector_may_add(sentences[i]):
            sentences[i] = sentences[i].replace("?", ".", 1)
    return "".join(sentences)


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
        # Module-level on purpose: constrain() is also called straight from the
        # tests and from the page, without a Polisher at hand.
        global questions_mode
        questions_mode = str(p.get("questions", "heard")).strip().lower()
        self.terms = terms
        self.allowed = allowed_words(terms, fixes)
        self.protected = protected or set()
        self.available = False
        self.reason = "not checked yet"
        self._next_check = 0.0  # do not hammer a dead LM Studio on every take
        self._probing = False   # a background re-check is already in flight
        self._loaded_seen = 0.0  # when the list of loaded models was last read
        self.on_status = None   # optional: called when availability changes
        # A bearer key for servers that want one (llama-server does, LM Studio
        # does not): either the key itself or a path to a file holding it.
        key = str(p.get("api_key", "") or "").strip()
        key_file = str(p.get("api_key_file", "") or "").strip()
        if not key and key_file:
            try:
                key = pathlib.Path(key_file).expanduser().read_text(encoding="utf-8").strip()
            except OSError as exc:
                key = ""
                print(f"[polish] WARNING: api_key_file {key_file} is not readable ({exc}); "
                      "requests go without a key and the server will answer 401", file=sys.stderr, flush=True)
        headers = {"Authorization": f"Bearer {key}"} if key else None
        self._client = httpx.Client(timeout=self.timeout, headers=headers)

    @property
    def system_prompt(self) -> str:
        base = SYSTEM_CLEAN if self.mode == "clean" else SYSTEM_LIGHT
        if not self.terms:
            return base
        return base + "\n\nСписок названий и терминов:\n" + ", ".join(self.terms[:150])

    def loaded_models(self, timeout: float = 2.0) -> tuple[list[str], str]:
        """Chat models that are IN MEMORY right now. Never anything else.

        The owner's rule, 2026-08-20: the model he loaded by hand is the one he
        works with, it must stay, and dictation must not drag a second one into
        VRAM. Asking LM Studio for a model that is merely installed makes it
        load that model — 18.5 GB and eleven seconds, over a four-second
        timeout, which is exactly how three takes in a row came out unpolished
        that evening.

        Hence /api/v0/models, which reports "state" for each entry. The old
        /v1/models cannot be used for the choice: it lists everything ever
        downloaded, loaded or not, and looks identical either way.
        """
        try:
            r = self._client.get(f"{self.base}/api/v0/models", timeout=timeout)
            r.raise_for_status()
            data = r.json().get("data", [])
        except Exception as exc:
            # Older LM Studio has no /api/v0. There /v1/models did list only
            # what was loaded, so the old behaviour is the right fallback.
            try:
                r = self._client.get(f"{self.base}/v1/models", timeout=timeout)
                r.raise_for_status()
                ids = [m.get("id", "") for m in r.json().get("data", [])]
                chat = [i for i in ids if i and "embed" not in i.lower()]
                return sorted(chat), "" if chat else "LM Studio has nothing loaded"
            except Exception:
                return [], f"LM Studio is not answering ({type(exc).__name__})"
        chat = [
            m.get("id", "")
            for m in data
            if m.get("state") == "loaded"
            and m.get("type") in ("llm", "vlm")
            and m.get("id")
        ]
        if not chat:
            return [], "LM Studio has no chat model in memory"
        return sorted(chat), ""

    def check(self, force: bool = False) -> bool:
        """Is there a live LM Studio with a model already in memory."""
        if not self.enabled:
            self.available, self.reason = False, "disabled in the settings"
            return False
        if not force and time.time() < self._next_check:
            return False
        self._next_check = time.time() + 30.0
        chat_ids, why = self.loaded_models()
        if not chat_ids:
            self.available, self.reason = False, why
            return False
        if not self.model or self.model not in chat_ids:
            self.model = chat_ids[0]
        self.available, self.reason = True, "ok"
        self._loaded_seen = time.time()
        self._next_check = 0.0
        return True

    def still_loaded(self, max_age: float = 20.0) -> bool:
        """Is the chosen model still in memory — checked before every request.

        He may swap models at any moment; whatever is in memory now is what
        dictation must use. The check costs about 15 ms on this machine and
        happens at most once every max_age seconds.
        """
        if time.time() - self._loaded_seen < max_age:
            return True
        chat_ids, why = self.loaded_models(timeout=1.0)
        self._loaded_seen = time.time()
        if not chat_ids:
            self.available, self.reason = False, why
            return False
        if self.model not in chat_ids:
            self.model = chat_ids[0]
        self.available, self.reason = True, "ok"
        return True

    def list_models(self) -> tuple[list[str], str]:
        """(models the page may offer, or why there are none).

        Only what is in memory: picking a model on the page must not load one.
        """
        return self.loaded_models()

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
                },
                timeout=15.0,
            )
        except Exception:
            pass
        return time.perf_counter() - t0

    def probe_soon(self) -> None:
        """Re-checks LM Studio in the background, never on the dictation path.

        Reaching a dead LM Studio is not free: a refused connection to
        localhost costs a full 2.0 s on this machine (measured 2026-08-20 on
        five different closed ports). Paying that while the person waits for
        their text made every first take after a 30-second pause 2.1 s instead
        of 0.15 s. So the check moved off the hot path: the take pastes raw
        text at once, and the corrector comes back by itself on the next one.
        """
        if self._probing or not self.enabled:
            return
        if time.time() < self._next_check:
            return
        self._probing = True

        def run() -> None:
            try:
                if self.check(force=True):
                    self.warmup()
                    if self.on_status:
                        self.on_status(True, self.model)
            except Exception:
                pass
            finally:
                self._probing = False

        threading.Thread(target=run, daemon=True).start()

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
        if not self.available:
            self.probe_soon()
            return raw, 0.0, self.reason
        # The model may have been swapped since the last take. Asking for one
        # that is no longer in memory would make LM Studio load it.
        if not self.still_loaded():
            self.probe_soon()
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
            # No "ttl" here on purpose. It sets an idle timer on the model, and
            # the model in memory is the owner's, not ours: he asked for it to
            # stay loaded always (2026-08-20). Nothing we send may unload it.
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
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                # /v1/models answers without a key, so the liveness check cannot see this;
                # only a real request can. Hold the verdict for 5 minutes instead of flapping.
                self.reason = "server rejected the key (check [polish] api_key_file)"
                self._next_check = time.time() + 300.0
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
