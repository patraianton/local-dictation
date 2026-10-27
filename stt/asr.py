# -*- coding: utf-8 -*-
"""Speech recognition: faster-whisper on the GPU."""
import collections
import math
import re
import time

import numpy as np

from . import cuda_fix

cuda_fix.enable()

from faster_whisper import WhisperModel  # noqa: E402

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def build_prompt(terms: list[str], limit: int, style: str = "sample") -> str:
    """The hint given to the recognizer.

    It does two things at once: teaches the model your words AND sets the
    writing style — capitals and punctuation. Without a hint the output is one
    long lowercase blur.
    """
    picked = [t for t in terms[:limit] if t]
    if not picked:
        return ""
    if style == "commands":
        # Imperative verbs in the hint fight the most damaging error there is:
        # the recognizer hears "сделай" (do it) as "сделаю" (I will do it) and
        # inverts the meaning. Measured on 383 takes: without them, 4 orders
        # lost at 17.0% word error; with these verbs, 2 lost at 17.4%. A variant
        # using whole sentences lost only 1 but scored 18.2% — content words
        # from the hint started leaking into the transcript. Hence verbs only,
        # no nouns.
        return (
            "Сделай, проверь, посмотри, запусти, запускай, отправляй, продолжай, "
            "отгружай, собери, поставь, обнови, покажи, найди, добавь, открой. "
            "Термины и названия: " + ", ".join(picked) + "."
        )
    if style == "list":
        return "Термины и названия: " + ", ".join(picked) + "."
    # A natural sentence in the speaker's own style beats a dry list: the model
    # copies not just the words but the formatting too.
    return (
        "Окей, смотри: закинь этот worktree в Claude Code, поставь loop на пять часов, "
        "потом глянь Intercom и Mailflow, и обнови дашборд в PostHog. "
        "По signups и MRR за неделю дай отдельную табличку. "
        "Термины: " + ", ".join(picked) + "."
    )


SENTENCE_MARKS = ".!?…"


def _with_mark(text: str, mark: str) -> str:
    """The same phrase with its final mark replaced."""
    t = text.rstrip()
    while t and t[-1] in SENTENCE_MARKS:
        t = t[:-1].rstrip()
    return t + mark


# One letter, or a pair or three of them, said over and over inside one word:
# the same loop at the level of letters. 12.10.2026: an 8 s take with a
# stammer at the start ("А, и... и... иди сам вставь...") came out as "Аааа, "
# and one "и" repeated four hundred times; it took 17 s to read, every word of
# it was lost, and the word-level check below saw only two words. A drawn-out
# "ааааа" or "хммм" in speech stays well under this.
LETTER_LOOP_RE = re.compile(r"(\w{1,3}?)\1{11,}", re.UNICODE)


def collapse_letter_loops(text: str) -> str:
    """What every retry still looped on, cut down to three of its unit: a wall
    of one letter helps nobody, and the words around it are still worth having."""
    return LETTER_LOOP_RE.sub(lambda m: m.group(1) * 3, text or "")


def looped(text: str, times: int = 4) -> bool:
    """Detects a decoding loop: the same word triple repeating over and over.

    A real failure mode of the recognizer — it slides into
    "Харьков, Мори, Харьков, Мори..." and fills half the screen with garbage.
    The same happens inside a single word (LETTER_LOOP_RE).
    """
    if LETTER_LOOP_RE.search(text or ""):
        return True
    words = PUNCT_RE.sub(" ", text.lower()).split()
    if len(words) < times * 3:
        return False
    seen: dict[tuple, int] = {}
    for i in range(len(words) - 2):
        gram = (words[i], words[i + 1], words[i + 2])
        seen[gram] = seen.get(gram, 0) + 1
        if seen[gram] >= times:
            return True
    return False


# Lines the recognizer says when it hears no speech and has to say something.
# They come from the subtitle files it was trained on — the credits at the end
# of a fansubbed video — and they arrive with full confidence, indistinguishable
# from a real take. Found nine times in eighteen days of logs, the worst on
# 25.08.2026: a takeover of a FOUR-MINUTE recording (237.99 s) that came out as
# the three words "Продолжение следует...". The last one was 29.08 at 09:10.
# Matched on letters only, so punctuation and case cannot smuggle one through.
GHOSTS = (
    "субтитры создавал", "субтитры делал", "субтитры сделал",
    "субтитрысоздал", "редактор субтитров", "корректор субтитров",
    "продолжение следует", "подписывайтесь на канал",
    "спасибо за просмотр", "das war ein kleiner test",
)


# The credits line with the names, the commonest live shape: an editor and a
# proofreader, each with initials ("Редактор субтитров А.Семкин Корректор
# А.Егорова", 25.08 and 10.09.2026). The letters-only match above gives up at
# 24 letters after the phrase, and this line has 29 (the 10.09 take, 3.75 s,
# was pasted as speech). So it is matched as written: an initial, a capital
# letter and a full stop, is the one thing that tells the line from speech.
CREDITS_RE = re.compile(
    r"\s*(?i:(?:редактор|корректор) субтитров)"
    r"(?:[\s,;]+(?:(?i:(?:редактор|корректор)(?:\s+субтитров)?)"
    r"|(?:[А-ЯЁA-Z]\.\s*){1,2}[А-ЯЁA-Z][а-яёa-z-]+))*[\s.!?…]*$"
)


def subtitle_ghost(text: str) -> str:
    """The stock phrase this take turned out to be, or "" if it is real speech.

    Only whole takes count. A ghost never comes with anything else around it —
    the recognizer either heard speech or filled the silence — so a take that
    merely mentions subtitles ("сделай субтитры к ролику") is left alone.
    """
    plain = PUNCT_RE.sub(" ", (text or "").lower())
    plain = " ".join(plain.split())
    if not plain:
        return ""
    for ghost in GHOSTS:
        if plain == ghost or plain.startswith(ghost + " ") and len(plain) <= len(ghost) + 24:
            return ghost
    if CREDITS_RE.match(text):
        return " ".join(plain.split()[:2])
    return ""


# A cough, a hum or a laugh written down as a word. When it is the whole take
# there is nothing to paste: "Кхм." landed in a chat window on 06.10.2026, and
# over the two months before that "Ммм.", "Ммм..." and "Ахаха." had come out as
# whole takes eight times, not one of them sent on. Answers that are words —
# "ага", "угу", "а" — are not sounds and stay. A drawn-out "Ааа..." is a sound:
# 17 whole takes of it from 14.08 to 08.10.2026, the two checked against the
# sent messages never sent. "ООО" is not on the list: it is also a company form.
SOUND_TOKEN_RE = re.compile(
    r"к?х+[мк]+|кх+е*|г+м+|м{2,}|э{2,}м*|хм+|п+ф+|а?(?:ха){2,}|ах(?:ах)+а?|а{3,}"
)


def just_a_sound(text: str) -> bool:
    """The whole take is a cough, a hum or a laugh and nothing else."""
    plain = PUNCT_RE.sub(" ", (text or "").lower()).split()
    # One letter drawn out with hyphens, "А-а-а-а-а-а..." (27.10.2026, pasted):
    # it falls apart into six one-letter words, more than the three below.
    if len(plain) > 3 and len(set(plain)) == 1 and len(plain[0]) == 1:
        return bool(SOUND_TOKEN_RE.fullmatch("".join(plain)))
    if not 0 < len(plain) <= 3:
        return False
    # "Ха-ха-ха" falls apart into three "ха", and one "ха" alone is a word.
    return all(SOUND_TOKEN_RE.fullmatch(w) for w in plain) or bool(
        SOUND_TOKEN_RE.fullmatch("".join(plain)))


# The same stock lines stuck onto the END of a long take, after real speech —
# four times in August and September, every one on a take of 30 s or more
# ("...и так далее. Продолжение следует..."). They come from a last window that
# holds almost no sound. Only the credits and "to be continued":
# "подписывайтесь на канал" can end a real script.
# A full stop right after a one-letter word is an initial, not the end of the
# line ("А.Семкин"): without that the match died at the first initial and the
# whole credits line stayed on the take.
GHOST_TAIL_RE = re.compile(
    r"[\s,;:—–-]*(?:(?:продолжение следует|субтитры (?:создавал|делал|сделал)"
    r"|редактор субтитров|корректор субтитров)(?:[^.!?…]|(?<=\b\w)\.)*[.!?…]*\s*)+$",
    re.IGNORECASE,
)


def strip_ghost_tail(text: str) -> str:
    """The take without a stock subtitle line on its end. Whole-ghost takes
    are left to subtitle_ghost: nothing real would remain here."""
    cut = GHOST_TAIL_RE.sub("", text or "").rstrip()
    if not cut or cut == (text or "").rstrip():
        return text
    return cut if cut[-1] in SENTENCE_MARKS else cut + "."


# A last window of a long take that holds almost no sound — a sliver left after
# a window that ran the full 30 s — is where the recognizer makes a line up.
# 24.10.2026: a 30.6 s take whose first window ended at 30.38 s; the 0.8 s left,
# the end of one word, came out as a video sign-off line said twice over, and
# the same sliver read on the Mac gave a subtitle credits line. Not a stock
# line that a list could catch: any line. But a window only hears the sound from where the one before stopped, so
# a text far longer than that sound can hold was not said. Speech in October
# 2026 ran at 10 letters a second (median of whole takes), 15.7 at the 99th
# percentile; 20 a second plus six letters is a word or two more than anybody
# says in that time.
TAIL_LETTERS_PER_S = 20.0
TAIL_SLACK_LETTERS = 6
TAIL_MAX_S = 2.0
LETTER_RE = re.compile(r"[^\W_]", re.UNICODE)


def cut_invented_tail(segments: list, total_s: float) -> tuple[list, str]:
    """(the segments without a made-up end, the text that was cut off).

    Only a segment after another one is judged: the sound it may hold runs
    from where the previous segment ended to the end of the take (trailing
    silence included, which only makes the check milder).
    """
    kept, cut = list(segments), []
    while len(kept) >= 2 and len(cut) < 2:
        last, before = kept[-1], kept[-2]
        sound = max(0.0, total_s - float(before.end))
        letters = len(LETTER_RE.findall(last.text or ""))
        if sound >= TAIL_MAX_S or letters <= TAIL_SLACK_LETTERS + TAIL_LETTERS_PER_S * sound:
            break
        cut.insert(0, (last.text or "").strip())
        kept.pop()
    return kept, " ".join(cut)


# A dash in front of the whole take: the recognizer writing it as a line of
# dialogue ("— Значит, ..."). Five takes from 02.09 to 02.10.2026, each with
# the dash before the first word. Dictation is never dialogue. polish already
# drops such a dash when the corrector adds it, but kept one the recognizer had
# written. A minus before a number ("-5") has no space after it and stays.
LEAD_DASH_RE = re.compile(r"^\s*[—–-]\s+")


def strip_dialogue_dash(text: str) -> str:
    return LEAD_DASH_RE.sub("", text or "", count=1)


# --- A capital where a long take was cut into pieces --------------------------
# A long take is read in pieces, and every piece starts with a capital even when
# it starts mid-sentence: "мы закрыли склад, потому что Это был…", "запусти помощников
# Проверить…". In October 2026, 7 of the 8 such capitals with no punctuation in
# front of them were in takes of more than 30 s; in short takes they were names
# and brands. The capital is lowered only where two pieces meet, the piece
# before ends with no full stop, and the word is an ordinary one, not a name.
SENTENCE_END = (".", "!", "?", "…")
FIRST_WORD_RE = re.compile(r"([А-ЯЁ][а-яё]*)(?![\w'’-])")


def join_pieces(texts) -> str:
    from .endings import ordinary_word
    out: list[str] = []
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        m = FIRST_WORD_RE.match(text)
        if out and m and not out[-1].endswith(SENTENCE_END) and ordinary_word(m.group(1)):
            text = m.group(1).lower() + text[m.end():]
        out.append(text)
    return " ".join(out)


# --- The hint read back instead of the last words -----------------------------
# With hint_every_window the hint stands in front of every 30-second window, the
# last one included, and that one is often a second or two of sound. Given so
# little to write, the recognizer sometimes carries on with the hint: the first
# terms of the glossary come out where the last words should be. Measured
# 01.10.2026 on 139 takes of more than 30 s, each read the old way twice and the
# new way once: 3 of the new reads (2%) ended with the first names of the hint
# read back, one of them with a credits line behind them; the two old reads
# never did. Two of the three had lost their last real word to it.
HINT_MARKS = ("Термины и названия:", "Термины:")


def hint_terms(hint: str) -> list[str]:
    """The glossary terms written into a hint, in the order they are written.
    Read from the hint itself, so it stays right when the app rebuilds the hint
    after a word is added."""
    for mark in HINT_MARKS:
        if mark in hint:
            tail = hint.rpartition(mark)[2].strip().rstrip(".")
            return [t.strip() for t in tail.split(", ") if t.strip()]
    return []


# What may follow the echo: a name or two the recognizer made up to go on with
# the list (one take had one more name after the hint's own), and the final mark.
ECHO_REST_RE = re.compile(r"(?:\s*,\s*[^\s,.!?…]+(?:\s[^\s,.!?…]+){0,2}){0,2}[\s.!?…]*")


def hint_echo_at(text: str, hint: str) -> int:
    """Where the end of `text` starts reading the hint back, or -1.

    An echo is two or more terms that stand next to each other in the hint,
    in the hint's order, separated by commas, with nothing but the final mark
    (and at most two made-up items) after them. A real take can end like that
    too (a list of two tools said aloud), so this only says "look again": the
    caller reads the take the old way and believes the echo only if that read
    does not end the same.

    The heading of the list read back counts too: on 13.10.2026 a 49 s take
    read by large-v3 ended in "Термины и названия." three times over, words
    nobody had said in the 10,000 takes before it.
    """
    terms = hint_terms(hint)
    best = -1
    for mark in HINT_MARKS:
        if mark in hint:
            label = re.escape(mark.rstrip(":"))
            m = re.search(rf"(?:[\s,.:;]*(?<!\w){label}[.:;]?)+[\s.!?…]*$", text or "",
                          re.IGNORECASE)
            if m:
                best = m.start()
            break
    for i in range(len(terms) - 1):
        pair = re.compile(
            r"(?<!\w)" + re.escape(terms[i]) + r"\s*,\s*" + re.escape(terms[i + 1]) + r"(?!\w)",
            re.IGNORECASE,
        )
        for m in pair.finditer(text):
            end, j = m.end(), i + 2
            while j < len(terms):
                more = re.compile(r"\s*,\s*" + re.escape(terms[j]) + r"(?!\w)", re.IGNORECASE)
                nxt = more.match(text, end)
                if not nxt:
                    break
                end, j = nxt.end(), j + 1
            if ECHO_REST_RE.fullmatch(text, end) and (best < 0 or m.start() < best):
                best = m.start()
    return best


# --- Text that is short for the speech it came from --------------------------
# The first pass can leave speech unread: it writes "end of text" at a pause, or
# skips a stretch in the middle. Nothing in it checks that the text covers the
# speech. This is a check that costs next to nothing on a normal take: only a
# take whose text is short for its length is looked at by the speech filter, and
# only one that has seconds of speech the text cannot hold is read again.
#
# Syllables per second of SPEECH (what the speech filter keeps), over 1,982
# turbo takes from 10.09-01.10.2026: median 4.1, 90th percentile 5.3. A text
# that would need more than FAST_RATE to be said cannot have been said in less.
FAST_RATE = 5.0
# The penalty on repeated words a looped first reading is retried with. It was
# the setting for every take until 08.10.2026, when it was measured: on 300
# turbo takes of 5 s and more it dropped real speech — repeated words the
# speaker did say, fillers, once a whole clause ("и затащить в ту категорию").
# Without it the text had more words in 62 takes and fewer in 30; three blind
# judges preferred it in 64 of the 131 takes that differed, the penalty in 17.
# On 300 short takes 289 came out word for word the same. Without the penalty
# one take of the 300 long ones looped; with it, none — hence this retry.
LOOP_PENALTY = 1.15
# Seconds of speech the text cannot hold before the take is read again. Of 1,607
# takes of 3.5-25 s, the one sure loss (a nine-word sentence skipped in the
# middle) had 12 s of it; the next largest case, a speaker repeating himself,
# 6.8 s. Takes flagged: 12% at 3.5 s, 5% at 5 s, 2.6% at 6 s — mostly slow
# talkers, whose speech is simply long for its words.
UNCOVERED_MIN_S = 6.0
# The second look is for takes up to this long, whatever else is set: the 1,607
# takes it was measured on stop here, and a longer one has been read with
# timestamps already (see timestamps_after_s) or, with that at 0, is a take the
# timestamp mode must not touch (it looped on a five-minute one on 22.09).
REREAD_MAX_S = 25.0
# The re-read replaces the text only if it is clearly fuller (clearly_fuller).
# Read with timestamps, all 1,607 takes: 839 came out identical, 7 clearly
# fuller by the thresholds below. One of the 7 was the real loss; the other six
# put back the speaker's own hesitations, repeated words and oaths that the
# first pass leaves out (a more literal reading, not more speech), or guessed
# one word differently.
NEW_WORDS_MIN = 3        # meaningful words the old text does not have
KEEP_SHARE_MIN = 0.8     # of the old words that must come back
GROWTH_MIN = 1.1         # length in letters, new over old
# Words that add nothing to what was said. A re-read that only puts back a
# "вот" or an oath the first pass left out is not fuller.
FILLERS = frozenset(
    "эээ ээ эм ну вот типа короче блядь блять блин так значит ага угу окей okay".split()
)

SYLLABLE_RE = re.compile(r"[аеёиоуыэюяaeiouy]", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+")
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
LATIN_RE = re.compile(r"[A-Za-z]")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def syllables(text: str) -> int:
    """About how many syllables the text takes to say: its vowels, and a number
    counts as three (digits carry no vowels, "30" is spoken as a word)."""
    t = text or ""
    return len(SYLLABLE_RE.findall(t)) + 3 * len(NUMBER_RE.findall(t))


def speech_chunks(audio: np.ndarray) -> list[tuple[int, int]]:
    """The stretches of the take the recognizer really listens to: the same
    filter, with the same settings, as inside transcribe. 8.6 ms for an average
    take, on the processor (4,181 takes, September 2026)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    found = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=300), sampling_rate=16000
    )
    return [(int(c["start"]), int(c["end"])) for c in found]


def heard_nothing(audio: np.ndarray) -> bool:
    """No speech at all in a quiet take as the microphone gave it.

    A quiet take is pulled up before it is recognized (audio.normalize), and an
    empty one pulled up seven times hisses loudly enough for the recognizer to
    hear a word in it: "Yep." was pasted from 2 s of silence (09.10.2026). The
    speech filter is run on the sound before that. Of 9,615 pasted takes
    (14.08-09.10.2026), 9 have no speech there, and all 9 were inventions:
    subtitle credits, "Музыка.", "Ммм.", "Yep." — not one real phrase. A loud
    take is not pulled up, and the recognizer's own filter already sees it as is.
    """
    from .audio import BOOST_BELOW

    if audio.size == 0 or float(np.abs(audio).max()) >= BOOST_BELOW:
        return False
    return not speech_chunks(audio)


# Below this many seconds of speech, a take that ran into max_seconds was a
# recording somebody forgot, not a dictation. See forgotten_take.
FORGOTTEN_SPEECH_S = 30.0


def forgotten_take(audio: np.ndarray) -> bool:
    """A take that ran into the maximum length with almost nobody speaking in it.

    Only asked about takes stopped by the maximum-length safety net. Those end
    there because nobody stopped them: the hands-free silence rules count
    every noise in the room as a voice, so a door or a chair every minute
    keeps such a take alive to the end. Of the 10 takes of 4+ minutes
    (24.08-10.10.2026), the 7 real dictations had 47-282 s of speech; the 3
    forgotten ones had 1-5 s, all of it somebody talking in the room, and
    that was what got pasted. 15 s sat three times away from both.

    Raised to 30 s on 13.10.2026: a call in the room with 17 s of speech in
    300 s ran past 15 s and 52 words of it were pasted. The two real
    dictations that ran into the limit since August had 215 and 281 s of
    speech; the least speech in any other such take was 47 s.
    """
    if audio.size == 0:
        return False
    speech = sum(e - s for s, e in speech_chunks(audio)) / 16000.0
    return speech < FORGOTTEN_SPEECH_S


def uncovered_s(text: str, chunks: list[tuple[int, int]]) -> float:
    """Seconds of speech beyond what the text can account for, at the fastest
    plausible rate. A text of N syllables needs at least N / FAST_RATE seconds."""
    speech = sum(e - s for s, e in chunks) / 16000.0
    return speech - syllables(text) / FAST_RATE


def _stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


def _meaning_stems(text: str) -> list[str]:
    """The words that carry meaning, as five-letter stems: a case ending or the
    spelling of a term must not make an old word look new."""
    return [_stem(w) for w in WORD_RE.findall(text) if len(w) > 2 and w.lower() not in FILLERS]


def _letters(text: str) -> int:
    return len(re.findall(r"[^\W_]", text))


def _latin_share(text: str) -> float:
    lat, cyr = len(LATIN_RE.findall(text)), len(CYRILLIC_RE.findall(text))
    return lat / (lat + cyr) if lat + cyr else 0.0


def clearly_fuller(old: str, new: str) -> bool:
    """Is `new` the same speech as `old`, with clearly more of it?

    All of these must hold:
    - at least NEW_WORDS_MIN different meaningful words that `old` does not have
      at all (filler words and oaths do not count, and neither does a word or a
      clause said twice: a stutter has no new word in it);
    - `old` is still in there: KEEP_SHARE_MIN of its words come back, so this is
      the same reading with something added, not a different reading;
    - it is at least GROWTH_MIN times as long in letters, so a term written as
      three words instead of one cannot pass for news.
    Refused whatever the counts say: a loop, a stock subtitle line, and a text
    in the other alphabet (the recognizer, told to expect Russian, writes an
    English sentence in Russian letters or translates it: the same speech
    again, not more of it).
    """
    if not new.strip() or looped(new) or subtitle_ghost(new) or strip_ghost_tail(new) != new:
        return False
    plain = " ".join(PUNCT_RE.sub(" ", new.lower()).split())
    if any(g in plain for g in GHOSTS):
        return False
    if abs(_latin_share(new) - _latin_share(old)) > 0.5:
        return False
    had, has = collections.Counter(_meaning_stems(old)), collections.Counter(_meaning_stems(new))
    # A set difference, not a count: "собери, собери" brings one more occurrence
    # of a word `old` already has, and the Counter difference took that for news.
    if len(set(has) - set(had)) < NEW_WORDS_MIN:
        return False
    if had and sum(min(c, has[w]) for w, c in had.items()) < KEEP_SHARE_MIN * sum(had.values()):
        return False
    return _letters(new) >= GROWTH_MIN * _letters(old)


class Asr:
    def __init__(self, cfg: dict, terms: list[str]):
        a = cfg["asr"]
        self.language = a.get("language", "ru")
        self.beam_size = int(a.get("beam_size", 1))
        self.vad = bool(a.get("vad", True))
        self.prompt = build_prompt(
            terms, int(a.get("prompt_terms", 45)), a.get("prompt_style", "sample")
        )
        self.device = a.get("device", "cuda")
        self.compute_type = a.get("compute_type", "float16")
        self.model_name = a.get("model", "large-v3-turbo")
        # A second, heavier model for SHORT takes only. A one or two second
        # command is where turbo breaks: there is almost no context to lean on,
        # and it guesses. Measured over 258 short takes from 05-10.09.2026, on
        # the 115 where the two models disagreed, twelve blind judges preferred
        # large-v3 in 32 takes against turbo's 14 — and the ones it fixed are
        # exactly the ones that had to be re-dictated: "Чиньёба цикета" ->
        # "Чинь оба тикета",
        # "Аксим" -> "Максим", "Версара" -> "Vercel", "лавиш" -> "Lavish".
        # On long takes it is the other way round, and four times slower, so
        # the switch is by length. "" turns it off.
        self.short_model_name = str(a.get("short_model", "") or "")
        self.short_seconds = float(a.get("short_seconds", 3.0))
        # Takes longer than this are read WITH timestamps. The recognizer reads
        # sound in 30-second windows; without timestamps it cannot say where it
        # stopped, so whatever it did not finish in one window is skipped and
        # the next window starts after it. Measured 30.09.2026 over September:
        # of 136 takes with more than 30 s of speech, 78 lost words that way,
        # about 1,300 in all — a whole last sentence would come out as its final
        # word alone, and some takes got "Продолжение следует..." pasted onto
        # the end. Twelve blind judges, on the takes where the two ways differ:
        # timestamps better 89 times, the old way 9. With timestamps the
        # next window starts where the words really ended. Short takes stay as
        # they were: there it changes nothing measurable. 0 = never.
        self.timestamps_after_s = float(a.get("timestamps_after_s", 25.0))
        # With condition_on_previous_text off, faster-whisper hands the hint to
        # the FIRST 30-second window only: from the second window on the prompt
        # is empty (transcribe.py, prompt_reset_since), so the rest of a long
        # take is read without the glossary and without the punctuation style
        # the hint carries. The same text passed as `hotwords` is put in front
        # of EVERY window instead. Window one gets the very same tokens (the
        # hint is 205 tokens, the limit 223; bench/test_hint_windows.py pins
        # that against the installed library) and the speed does not change.
        # Measured 01.10.2026 on the 139 takes since 01.09 with more than 30 s
        # of speech: window one read identically in 137 (the other two are runs
        # that differ from each other, with no change of settings at all, a loop
        # of credits and a take in English); 118 takes differ later on, 21 of
        # them only in punctuation and capitals. Over all 139: a lowercase letter
        # after a full stop 31 times against 10 (a seam between windows, gone),
        # commas per 100 words 12.4 against 13.7, 276 more words in all. The last
        # window, which used to be read bare, now carries the style too: takes
        # that ended with no final mark, 47 against 8, and glossary terms spelled
        # right, 148 against 118 (114 on a repeat of the old way). The price is
        # the hint read back in a nearly empty last window, 3 takes of 139; that
        # is caught and the take read the old way (hint_echo_at, _without_echo).
        # Applies to long takes only, the ones read with timestamps; a short take
        # has one window and nothing changes there.
        self.hint_every_window = bool(a.get("hint_every_window", True))
        # Read a take again, with timestamps, when its text is short for the
        # speech it holds, and keep the new text if it is clearly fuller (see
        # uncovered_s and clearly_fuller). Off in the code default: whether it
        # is worth turning on is decided on the measurements in config.toml.
        self.reread_early_end = bool(a.get("reread_early_end", False))
        # The penalty on repeated words for a normal reading (see LOOP_PENALTY).
        self.repetition_penalty = float(a.get("repetition_penalty", 1.0))
        self.last_reread: dict | None = None   # what the last take's second look did
        self.last_echo: dict | None = None     # what the last take's hint check did
        self.last_tail_cut: str | None = None  # a made-up end cut off the last take
        self.model = None
        self.short = None
        self.last_model = self.model_name   # which model did the last take
        self._tok = None                    # built on first use, see question_score

    def _open_model(self, name: str):
        """Loads one model. The disk copy first, the network never if avoidable."""
        for local_only in (True, False):
            try:
                return WhisperModel(
                    name, device=self.device, compute_type=self.compute_type,
                    local_files_only=local_only,
                )
            except Exception:
                continue
        return None

    def load(self) -> float:
        t0 = time.perf_counter()
        self.model = self._open_model(self.model_name)
        if self.model is None:
            # No GPU available — run on the CPU rather than not run at all.
            self.device, self.compute_type = "cpu", "int8"
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            return time.perf_counter() - t0
        if self.short_model_name and self.short_model_name != self.model_name:
            # Best effort: a second model is worth 3 GB of video memory, but not
            # worth refusing to start over. On the processor it would take
            # seconds per take, so there it is skipped.
            if self.device != "cpu":
                self.short = self._open_model(self.short_model_name)
        return time.perf_counter() - t0

    def pick(self, audio: np.ndarray, speech_s: float | None = None):
        """Which model this take goes to, and its name for the log.

        The length that decides is the SPEECH, not the file: every recording
        also carries half a second of pre-roll and up to 0.4 s of tail, and
        counting those would push a two-and-a-half-second phrase over a
        three-second threshold. That is exactly what happened to "Заводите
        китос, конечно" on 10.09.2026.
        """
        secs = len(audio) / 16000.0 if speech_s is None else speech_s
        if self.short is not None and secs <= self.short_seconds:
            return self.short, self.short_model_name
        return self.model, self.model_name

    def drop_short(self) -> bool:
        """Gives up the second model to free video memory.

        The card is shared with LM Studio, and the model sitting there is the
        owner's, not ours: he may swap a 3 GB corrector for a 30 GB one at any
        moment. On 26.08.2026 exactly that killed dictation — the recognizer
        lost the card and the process quietly died. So when memory runs out,
        the first thing to go is the extra model: short takes get worse, which
        is far better than dictation stopping.
        """
        if self.short is None:
            return False
        self.short = None
        return True

    def warmup(self) -> float:
        """The first run is always slow — get it over with, on silence."""
        t0 = time.perf_counter()
        self.transcribe(np.zeros(16000, dtype=np.float32))
        if self.short is not None:
            # A second of silence goes to the short-take model by length, so
            # this warms that one; a longer buffer warms the main one.
            self.transcribe(np.zeros(int(16000 * (self.short_seconds + 1)),
                                     dtype=np.float32))
        # Same for the weighing path: building the tokenizer costs 140 ms, and
        # without this the first take of the day would pay it.
        self.question_score(np.zeros(16000, dtype=np.float32), "Проверка связи.")
        return time.perf_counter() - t0

    @staticmethod
    def _hotwords(model, hint: str) -> str:
        """The hint in the shape `hotwords` wants.

        faster-whisper cuts a hint that is too long for the decoder's prompt
        from OPPOSITE ends: initial_prompt keeps its last max_length//2 - 1 = 223
        tokens, hotwords its first 223. Today's hint is 205 tokens, so neither
        cuts anything. A longer one (more terms, longer terms) would lose its
        terms under `hotwords` and its imperative verbs under initial_prompt, so
        window one would no longer read exactly as it did. Trimmed here the way
        initial_prompt would have trimmed it.

        By token ids, from a token that opens a word. The cut of the last 223
        tokens usually falls inside a word, and the text of such a tail, put
        back through the encoder behind the space faster-whisper adds, is a
        different token string: the first token shifts, the tail comes out one
        token long and the closing full stop is cut off. Measured with the
        large-v3-turbo tokenizer on 200 random hints of 241-296 tokens: the plain
        decode gave the initial_prompt tokens in 50 and lost the closing full
        stop in 57. Here the 0-4 tokens of the broken first word are dropped
        and every other token is the one initial_prompt would have kept.
        """
        try:
            limit = int(model.max_length) // 2 - 1
            tok = model.hf_tokenizer
            ids = tok.encode(" " + hint.strip(), add_special_tokens=False).ids
            if len(ids) <= limit:
                return hint
            for k in range(len(ids) - limit, len(ids)):
                if not tok.decode([ids[k]]).startswith(" "):
                    continue
                cut = tok.decode(ids[k:]).strip()
                if len(tok.encode(" " + cut, add_special_tokens=False).ids) <= limit:
                    return cut
            return tok.decode(ids[-limit:]).strip()
        except Exception:
            return hint

    def _segments(self, audio: np.ndarray, prompt: str | None, model=None,
                  timestamps: bool = False, every_window: bool = False,
                  penalty: float | None = None) -> list:
        model = model or self.model
        hint = prompt or None
        segments, _info = model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            # A list of temperatures gives the model a way out: if the
            # result looks like nonsense (over-compressed or improbable text)
            # it retries on its own.
            temperature=[0.0, 0.2, 0.4, 0.6],
            compression_ratio_threshold=2.4,
            repetition_penalty=self.repetition_penalty if penalty is None else penalty,
            condition_on_previous_text=False,
            # The same hint, one of two ways: as the prompt of the first window
            # only, or as hotwords in front of every window (hint_every_window).
            initial_prompt=None if every_window else hint,
            hotwords=self._hotwords(model, hint) if every_window and hint else None,
            vad_filter=self.vad,
            vad_parameters={"min_silence_duration_ms": 300},
            without_timestamps=not timestamps,
        )
        return list(segments)

    def _run(self, audio: np.ndarray, prompt: str | None, model=None,
             timestamps: bool = False, every_window: bool = False,
             penalty: float | None = None) -> str:
        segments = self._segments(audio, prompt, model, timestamps, every_window, penalty)
        if timestamps:
            segments, cut = cut_invented_tail(segments, len(audio) / 16000.0)
            if cut:
                self.last_tail_cut = cut
        return join_pieces(seg.text for seg in segments)

    # Marks of a GPU that fell away, leaving the in-memory model dead.
    # Happens after the machine sleeps: on 2026-08-19 the app hung like this for
    # four days, answering "FAILED" to every take until restarted by hand.
    LOST_GPU = ("cuda", "cudnn", "cublas", "gpu", "device-side", "out of memory")

    @classmethod
    def looks_like_lost_gpu(cls, exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(mark in text for mark in cls.LOST_GPU)

    def reload(self) -> str:
        """Brings the model back after the GPU was lost.

        Tries the same GPU first — usually the context just needs recreating.
        If that fails, falls back to the CPU: slower, but dictation stays alive.
        """
        self.model = None
        self.short = None
        self._tok = None
        try:
            self.load()
            return self.device
        except Exception:
            self.device, self.compute_type = "cpu", "int8"
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            return "cpu"

    def transcribe(self, audio: np.ndarray,
                   speech_s: float | None = None) -> tuple[str, float]:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        t0 = time.perf_counter()
        model, name = self.pick(audio, speech_s)
        self.last_model = name
        self.last_tail_cut = None
        long = 0 < self.timestamps_after_s < len(audio) / 16000.0
        # Only a long take has a window after the first, so only there does the
        # way the hint is passed matter. Nothing extra is passed otherwise.
        every = {"every_window": True} if long and self.hint_every_window else {}
        text = self._run(audio, self.prompt, model, timestamps=long, **every)
        # Looped with no penalty on repeated words: the same reading with it
        # first (LOOP_PENALTY), before the hint is suspected.
        if looped(text) and self.repetition_penalty < LOOP_PENALTY:
            text = self._run(audio, self.prompt, model, timestamps=long,
                             penalty=LOOP_PENALTY, **every)
        # Looped: almost always the hint is to blame. Retry without it.
        if looped(text):
            text = self._run(audio, None, model, timestamps=long)
        # Still looping with timestamps (22.09.2026: sixty "да" in a row on a
        # five-minute take) — the old way loses words but does not loop there.
        if long and looped(text):
            text = self._run(audio, self.prompt, model)
        text = collapse_letter_loops(text)
        self.last_echo = None
        if long:
            text = strip_ghost_tail(text)
            if every and hint_echo_at(text, self.prompt) >= 0:
                text = self._without_echo(audio, text, model)
        self.last_reread = None
        # By the length of the take, not by `not long`: with timestamps_after_s
        # at 0 nothing is "long", and a five-minute take must not be read again.
        if (self.reread_early_end and not long and model is self.model
                and len(audio) / 16000.0 <= REREAD_MAX_S):
            text = self._reread_early_end(audio, text, model)
        return strip_dialogue_dash(text), time.perf_counter() - t0

    def _without_echo(self, audio: np.ndarray, text: str, model) -> str:
        """The text of a long take whose end reads like the hint, checked.

        The take is read again the old way, with the hint as the first window's
        prompt only (the way every take was read until hint_every_window). If
        that text does not end in the hint too, the end of the first reading was
        the recognizer carrying on with the hint, and the old reading is used:
        it has the real last words. If it ends the same way, those words were
        said, and the first reading stays. It costs one more read of a take, on
        2% of the long ones.
        """
        try:
            again = strip_ghost_tail(self._run(audio, self.prompt, model, timestamps=True))
        except Exception as exc:
            # The take is pasted whatever happens here, except a lost video card.
            if self.looks_like_lost_gpu(exc):
                raise
            self.last_echo = {"used": "first", "why": "error"}
            return text
        if not again.strip() or looped(again) or hint_echo_at(again, self.prompt) >= 0:
            self.last_echo = {"used": "first", "why": "said"}
            return text
        self.last_echo = {"used": "old way", "why": "echo"}
        return again

    def _reread_early_end(self, audio: np.ndarray, text: str, model) -> str:
        """Reads the take again, with timestamps, if its text is short for its speech.

        The first gate is plain arithmetic on the text and the length of the
        take and returns at once for nine takes in ten; only a take that could
        hold that much speech goes to the speech filter; and only one with
        UNCOVERED_MIN_S seconds of speech the text cannot hold goes to the
        recognizer again. The new text replaces the old only if it is clearly
        fuller. Why timestamps: on the one sure loss found (a sentence skipped
        in the middle of a 22-second take, 25.09.2026) both timestamps and the
        larger model read it back, and timestamps keep the same model, so the
        wording does not shift. An "end of text" at a pause, the other thing
        this was built for, was NOT found: in 687 takes with pauses the speech
        after the pause was always the same words said again.
        """
        if not text.strip() or subtitle_ghost(text):
            return text
        if syllables(text) > FAST_RATE * (len(audio) / 16000.0 - UNCOVERED_MIN_S):
            return text
        try:
            gap = uncovered_s(text, speech_chunks(audio))
            if gap < UNCOVERED_MIN_S:
                return text
            again = self._run(audio, self.prompt, model, timestamps=True)
        except Exception as exc:
            # A take must be pasted whatever happens here — except that a lost
            # video card has to reach the code that brings the model back.
            if self.looks_like_lost_gpu(exc):
                raise
            return text
        better = clearly_fuller(text, again)
        self.last_reread = {"uncovered_s": round(gap, 1), "changed": better}
        return again if better else text

    # The longest take that can be weighed for a question. The recognizer reads
    # the sound in one 30-second window; a longer take would have its ending —
    # the only part that matters here — cut off.
    MAX_SCORE_SECONDS = 29.0

    def _tokenizer(self):
        from faster_whisper.tokenizer import Tokenizer

        if self._tok is None:
            self._tok = Tokenizer(
                self.model.hf_tokenizer, self.model.model.is_multilingual,
                task="transcribe", language=self.language,
            )
        return self._tok

    def question_score(self, audio: np.ndarray, text: str) -> float | None:
        """How much more the sound looks like a question than like a statement.

        The recognizer writes the final mark itself, in one pass, and whatever
        it decided is what stays — a question said without a question word
        ("Мы коробки сложили на эти полки?") comes out as a statement. But it
        can be asked differently: give it the SAME phrase twice, once ending in
        "." and once in "?", and make it read both against the recording. It
        returns how well each version agrees with the sound. The difference is
        the answer to "did the voice go up at the end".

        This is not pitch measured by hand (that was tried, bench/pitch.py, and
        it is weak): it is the same network that already hears intonation,
        simply asked directly.

        Returns log P(with "?") - log P(with "."). Above zero means the sound
        looks like a question. None when the take cannot be weighed (too long,
        no text, the model is busy elsewhere) — never raises: a take must be
        pasted even if this fails.

        Measured over 421 real takes on 08-10.09.2026, with the threshold at
        -1.0: five questions saved over three days and not one extra mark. See
        bench/qscore.py and bench/qpolicy.py.
        """
        if self.model is None or not text.strip():
            return None
        if len(audio) / 16000.0 > self.MAX_SCORE_SECONDS:
            return None
        try:
            from faster_whisper.audio import pad_or_trim

            tok = self._tokenizer()
            features = self.model.feature_extractor(audio)
            n_frames = features.shape[-1]
            enc = self.model.encode(pad_or_trim(features))
            start = list(tok.sot_sequence)

            def logprob(variant: str) -> float:
                tokens = tok.encode(" " + variant.strip())
                res = self.model.model.align(
                    enc, start, [tokens], n_frames, median_filter_width=7
                )[0]
                return sum(math.log(max(p, 1e-9)) for p in res.text_token_probs)

            return logprob(_with_mark(text, "?")) - logprob(_with_mark(text, "."))
        except Exception:
            return None
