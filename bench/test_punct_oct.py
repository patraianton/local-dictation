# -*- coding: utf-8 -*-
"""Punctuation fixes of 01.10.2026: marks the corrector invents, the cut-off "...",
a mark glued after a comma, and two holes in the lock.

Every phrase here is invented; the real cases that led to the rules are in the
journals and in the replay report, not in the tree.

    ..\\.venv\\Scripts\\python.exe test_punct_oct.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt import polish  # noqa: E402
from stt.polish import allowed_words, constrain, flip_question  # noqa: E402

bad = 0
total = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global bad, total
    total += 1
    bad += 0 if ok else 1
    print(("[v] " if ok else "[X] ") + name)
    if not ok and detail:
        print(detail)


# --- 1. may the corrector add a mark to this sentence -----------------------
KEEP = [
    ("Почему сервер не отвечает?", "an ordinary question word at the head"),
    ("Сколько стоит такой тариф?", "and another one"),
    ("Я не понимаю, почему отчёт пустой?", "'I do not understand why' stays: the owner's trade of 25.08"),
    ("Нужно ли нам это делать?", "'ли' asks, so a need word in front of it does not matter"),
    ("Не надо, зачем тебе этот доступ?", "a refusal followed by a question is not a statement of need"),
    ("Я тебя спросил, сколько там осталось?", "the speaker asking a second time is a question"),
    ("Ты же говорил, почему мы так делаем?", "said to the listener it is a question"),
    ("Когда пришли деньги?", "'пришли' right after the question word is the past tense, not an order"),
    ("Куда делись файлы?", "a short question with a verb the dictionary also reads as an order"),
    ("Как бы ты это сделал?", "'как бы' at the head is a real question"),
    ("Ну а как бы мы это сделали?", "'как бы' after nothing but fillers is one too"),
    ("Что у тебя с настройкой?", "'что' at the head"),
    ("Не проверено пока, где лежит архив?", "a complaint with 'не', then a real question"),
    ("Ты смотрел, сколько у нас осталось места?", "asking the listener what he did is a question"),
]
BLOCK = [
    ("Он спросил, где лежит файл?", "somebody else's question passed on"),
    ("Она спрашивает, есть ли у нас доступ?", "the same in the present tense"),
    ("Я же тебе писал, почему так нельзя?", "the speaker telling what he wrote is a statement"),
    ("Потому что я хочу проверить другой вариант?", "'потому что' is not a question word"),
    ("Только что опять пропала связь?", "'только что' is not a question word"),
    ("Знаешь что, открой там новую вкладку?", "'знаешь что' opens an order"),
    ("Это как типа вторая версия?", "'как типа' is 'like'"),
    ("Это как бы готово, но не совсем?", "a 'как бы' in the middle is 'sort of'"),
    ("Скорость больше, чем в описании?", "'чем' after a comparative is 'than'"),
    ("Мне надо понять, сколько их осталось?", "a statement of need"),
    ("Я хочу посмотреть, какие остались?", "a statement of wish"),
    ("А что за звук, расскажи, найди примеры?", "ends as an order"),
    ("Как дела и проверь, что там с логами?", "an order after 'и'"),
    ("Покажи мне, пожалуйста, где лежит файл?", "an order before the question word"),
    ("Я ищу, кто продал этот диван?", "what the speaker is doing: 'I am looking for who'"),
    ("Мы просто оцениваем, будет ли от этого толк?", "the same with 'ли'"),
    ("И посчитано, сколько стоит одна доставка?", "a task to be done, not a question"),
]
print("-- may the corrector add a mark")
for sentence, why in KEEP:
    got = polish.corrector_may_add(sentence)
    check(got is True, f"keeps: {sentence}  ({why})")
for sentence, why in BLOCK:
    got = polish.corrector_may_add(sentence)
    check(got is False, f"blocks: {sentence}  ({why})")

# --- 2. the same through the whole chain, in a take that has a heard mark ----
polish.questions_mode = "heard"
CHAIN = [
    (
        "an order in a take where the recognizer heard another question: its mark goes",
        "Привет, как дела? Посмотри там, сколько осталось.",
        "Привет, как дела? Посмотри там, сколько осталось?",
        "Привет, как дела? Посмотри там, сколько осталось.",
    ),
    (
        "a heard mark on an order-shaped sentence is not given away to a neighbour",
        "Иди проверь, есть ли там продажи? Почему так медленно.",
        "Иди проверь, есть ли там продажи? Почему так медленно?",
        "Иди проверь, есть ли там продажи? Почему так медленно?",
    ),
    (
        "reported speech with no heard mark at all",
        "Он спросил, где лежит файл.",
        "Он спросил, где лежит файл?",
        "Он спросил, где лежит файл.",
    ),
    (
        "a real question the recognizer missed is still restored",
        "Почему отчёт пустой.",
        "Почему отчёт пустой?",
        "Почему отчёт пустой?",
    ),
]
print("\n-- through the chain")
for name, raw, pol, want in CHAIN:
    got = polish.no_false_question(raw, pol)
    check(got == want, name, f"      wanted {want!r}\n      got    {got!r}")

# --- 3. the cut-off "..." survives the corrector -----------------------------
TERMS = ["AcmeDesk", "AcmePass", "Lavish", "GoHighLevel"]
ALLOWED = allowed_words(TERMS)
ELLIPSIS = [
    ("Можешь проверить...", "Можешь проверить.", "Можешь проверить...",
     "a full stop in place of the ellipsis is turned back"),
    ("Что значит от...", "Что значит от?", "Что значит от...",
     "so is a question mark"),
    ("Давай посмотрим вот это…", "Давай посмотрим вот это.", "Давай посмотрим вот это…",
     "the one-character ellipsis comes back as it was"),
    ("Можешь проверить...", "Можешь проверить...", "Можешь проверить...",
     "an ellipsis the corrector kept is not doubled"),
    ("Ну... давай так.", "Ну, давай так.", "Ну, давай так.",
     "only the END is looked at: an ellipsis in the middle is the corrector's business"),
    ("Проверь это, пожалуйста.", "Проверь это, пожалуйста?", "Проверь это, пожалуйста.",
     "no ellipsis in the raw text: nothing to give back"),
]
print("\n-- the cut-off ellipsis")
for raw, pol, want, why in ELLIPSIS:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 4. flipping a mark behind an unfinished one ------------------------------
FLIP = [
    ("Итак,", "Итак?", 1, "?", "a comma at the end is replaced, not followed"),
    ("Итак;", "Итак?", 1, "?", "a semicolon"),
    ("Итак:", "Итак?", 1, "?", "a colon"),
    ("Это —", "Это?", 2, "?", "a dash goes together with the space before it"),
    ("Это—", "Это?", 1, "?", "a dash with no space"),
    ("Итак, ", "Итак? ", 1, "?", "trailing whitespace stays where it was"),
    ("Скоро это закончится.", "Скоро это закончится?", 1, "?", "the plain case is untouched"),
    ("Убери loop", "Убери loop?", 0, "?", "no mark at all: just append"),
]
print("\n-- flip_question")
for said, want_text, want_erase, want_type, why in FLIP:
    got = flip_question(said)
    check(got == (want_text, want_erase, want_type), why,
          f"      wanted {(want_text, want_erase, want_type)!r}\n      got    {got!r}")

# --- 5. an order is not made out of a promise the speaker made ----------------
PERSON = [
    ("Ладно, я это отправлю завтра.", "Ладно, я это отправь завтра.",
     "Ладно, я это отправлю завтра.", "'я' before the verb in the same sentence: left as said"),
    ("Давай, мы потом сделаем, а я проверю.", "Давай, мы потом сделаем, а я проверь.",
     "Давай, мы потом сделаем, а я проверю.", "the same with a second verb"),
    ("Отправлю отчёт, чтобы я понял.", "Отправь отчёт, чтобы я понял.",
     "Отправь отчёт, чтобы я понял.", "'я' AFTER the verb does not protect it: still an order"),
    ("Я закончил. Отправлю отчёт.", "Я закончил. Отправь отчёт.",
     "Я закончил. Отправь отчёт.", "'я' in the previous sentence does not protect the next one"),
    ("Отправлю отчёт.", "Отправь отчёт.",
     "Отправь отчёт.", "no 'я' anywhere: the useful swap still works"),
]
print("\n-- the order flip and the speaker")
for raw, pol, want, why in PERSON:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 6. a word beside a term does not vanish with it ---------------------------
BYSTANDER = [
    ("Где они в Acme Desks стоят, в исключениях?", "Где они в AcmeDesk в исключениях?",
     "Где они в Acme Desks стоят, в исключениях?",
     "an ordinary verb beside a misspelled term is not swallowed"),
    ("Открой лавиш страница сейчас.", "Открой Lavish сейчас.",
     "Открой лавиш страница сейчас.",
     "the same when the term was heard in Cyrillic and the noun comes after it"),
    ("Открой лавиш сейчас.", "Открой Lavish сейчас.",
     "Открой Lavish сейчас.",
     "a term heard in Cyrillic is still replaced"),
    ("Открой акме паса сейчас.", "Открой AcmePass сейчас.",
     "Открой AcmePass сейчас.",
     "two heard words that together are one term still go through"),
    ("Там гоу хай левел стоит.", "Там GoHighLevel стоит.",
     "Там GoHighLevel стоит.",
     "three heard words, one term"),
]
print("\n-- bystanders")
for raw, pol, want, why in BYSTANDER:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 7. a word with its own dot, then a mark taken back ----------------------
DOTS = [
    ("Собери отчёты, таблицы и т.д.", "Собери отчёты, таблицы и т.д.?",
     "Собери отчёты, таблицы и т.д.", "no doubled dot after an abbreviation"),
    ("Это про пункт 2.", "Это про пункт 2.?",
     "Это про пункт 2.", "no doubled dot after a numbered item"),
    ("Это про пункт...", "Это про пункт...",
     "Это про пункт...", "an ellipsis stays an ellipsis"),
]
print("\n-- doubled dots")
for raw, pol, want, why in DOTS:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 8. a yo the corrector made up -------------------------------------------
YO = [
    ("самые проверенные способы", "Самые проверённые способы.",
     "Самые проверенные способы.", "a yo the dictionary does not know goes back to е"),
    ("еще все отчеты партнеров", "Ещё всё отчёты партнёров.",
     "Ещё всё отчёты партнёров.", "real yo words keep it"),
]
print("\n-- yo")
for raw, pol, want, why in YO:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 9. a sentence start keeps its capital after a stop ----------------------
CAPS = [
    ("Открой отчёт... Можно его, кстати, в таблицу вынести. Какой-то итог нужен.",
     "Открой отчёт, какой-то итог нужен.",
     "Открой отчёт... Можно его, кстати, в таблицу вынести. Какой-то итог нужен.",
     "the corrector glued two sentences; the lock put the stop back, so the capital too"),
    ("Отчёт готов. Что с остальными?", "Отчёт готов. что с остальными?",
     "Отчёт готов. Что с остальными?", "a capital taken away after a full stop comes back"),
    ("Я хочу... К примеру, вот это.", "Я хочу… к примеру, вот это.",
     "Я хочу… к примеру, вот это.", "after an ellipsis a small letter may stay"),
    ("Сделай Отчёт сегодня.", "Сделай отчёт сегодня.",
     "Сделай отчёт сегодня.", "lowercasing a capital in the middle of a sentence still goes through"),
]
print("\n-- capital after a stop")
for raw, pol, want, why in CAPS:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 10. no half-pair of quotes ----------------------------------------------
QUOTES = [
    ("Мне нравится там, good morning, have a nice day — это окей.",
     "Мне нравится такое: «good morning», «have a nice day» — это окей.",
     "Мне нравится там, good morning, have a nice day — это окей.",
     "the opening quote went with a rolled-back word: all the added quotes go"),
    ("Он сказал всё будет нормально.", "Он сказал: «всё будет нормально».",
     "Он сказал: «всё будет нормально».", "whole quotes stay"),
    ("Он сказал «готово» и ушёл.", "Он сказал «готово и ушёл.",
     "Он сказал «готово и ушёл.", "quotes the take came with are not touched"),
]
print("\n-- quotes")
for raw, pol, want, why in QUOTES:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 11. a hyphen the recognizer wrote stays -----------------------------------
HYPHEN = [
    ("По-моему это уже готово.", "По моему, это уже готово.",
     "По-моему, это уже готово.", "the hyphen in по-моему comes back, the corrector's comma stays"),
    ("Кто-то это-то уже сделал.", "Кто то это то уже сделал.",
     "Кто-то это-то уже сделал.", "every dropped hyphen comes back"),
    ("кто то звонил", "Кто-то звонил.",
     "Кто-то звонил.", "a hyphen the corrector adds is welcome"),
    ("Это по-моему готово.", "Это, по-моему, готово.",
     "Это, по-моему, готово.", "a hyphen the corrector kept is untouched"),
    ("Да-да, это уже сделано.", "Да — да, это уже сделано.",
     "Да-да, это уже сделано.", "a spaced dash between two Russian words goes back to the hyphen"),
    ("Стой-стой, я сам-то не понял.", "Стой— стой, я сам — то не понял.",
     "Стой-стой, я сам-то не понял.", "any spaced dash, either side"),
    ("Бери любой IP-Hetzner.", "Бери любой IP — Hetzner.",
     "Бери любой IP — Hetzner.", "between Latin words the dash may be a real reading"),
    ("Это займёт 2-3 дня.", "Это займёт 2–3 дня.",
     "Это займёт 2–3 дня.", "a range dash without spaces stays"),
]
print("\n-- hyphens")
for raw, pol, want, why in HYPHEN:
    got = constrain(raw, pol, ALLOWED)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

# --- 12. an ending of a replacement is not a term ---------------------------
# "AcmeHost'а" puts "а" and "у" into the allowed set, the way real replacements
# with an apostrophe ending do.
ALLOWED_ENDINGS = allowed_words(TERMS + ["AcmeHost'а", "AcmeHost'у"])
ENDINGS = [
    ("Так а что с отчётом?", "А что с отчётом?",
     "Так а что с отчётом?", "a short word is not a term, even if a replacement ending made it 'allowed'"),
    ("Есть у меня вопрос.", "Есть У меня вопрос.",
     "Есть у меня вопрос.", "the same for у"),
    ("спроси у acmehost", "Спроси у AcmeHost.",
     "Спроси у AcmeHost.", "a real term still keeps its capital"),
]
print("\n-- endings are not terms")
for raw, pol, want, why in ENDINGS:
    got = constrain(raw, pol, ALLOWED_ENDINGS)
    check(got == want, why, f"      said   {raw!r}\n      wanted {want!r}\n      got    {got!r}")

print(f"\n{total - bad} of {total} passed")
sys.exit(1 if bad else 0)
