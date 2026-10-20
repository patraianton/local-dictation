# -*- coding: utf-8 -*-
"""Checks the lock: the corrector must not be able to rewrite the speaker.

    ..\\.venv\\Scripts\\python.exe test_constrain.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import allowed_words, constrain  # noqa: E402

TERMS = ["AcmePass", "GitHub", "Claude Code", "Chrome Extension", "Supabase",
         "Intercom", "loop", "worktree", "Codex", "outbound", "Plan A"]
ALLOWED = allowed_words(TERMS)

# (what was said, what the corrector returned, what must come out, why)
CASES = [
    (
        "И где лежит её тетрадь?",
        "Где находится её тетрадь?",
        "И где лежит её тетрадь?",
        "do not change words, and do not drop the leading conjunction",
    ),
    (
        "Что за фигня, только что 8 окон github.exe открылось.",
        "Что за фиговня, только что 8 окон GitHub.exe открылось.",
        "Что за фигня, только что 8 окон GitHub.exe открылось.",
        "keep the slang intact, but take GitHub capitalized",
    ),
    (
        "Спланируй новых грядок и дорожек на акме паса, причем у забора.",
        "Спланируй новые грядки и дорожки на AcmePass, причём у забора.",
        "Спланируй новых грядок и дорожек на AcmePass, причём у забора.",
        "a term may be substituted, grammar may not be edited",
    ),
    (
        "открой github вот я тебе скачал chrome extension заведи там себе профиль",
        "Открой GitHub, вот я тебе скачал Chrome Extension. Заведи там себе профиль.",
        "Открой GitHub, вот я тебе скачал Chrome Extension. Заведи там себе профиль.",
        "punctuation and capitals are taken wholesale",
    ),
    (
        "Сделай там себе токены и все что тебе надо.",
        "Сделай там себе токены и всё, что тебе нужно.",
        "Сделай там себе токены и всё, что тебе надо.",
        "synonyms are not accepted; the e/yo spelling is left to the corrector",
    ),
    (
        "Иди собери контекст, отправляя агентов по outbound репозиторию.",
        "Иди собери контекст, отправляя агентов по Outbound репозиторию — "
        "нам надо собрать хорошее ICP.",
        # "outbound" is a term always written in lowercase, so the corrector's
        # capital on it mid-sentence does not stay either.
        "Иди собери контекст, отправляя агентов по outbound репозиторию.",
        "anything the model added on its own is dropped",
    ),
    (
        "Там Excel, Notion и так далее.",
        "Там Excel, Notion, Slack, Trello, Zoom, Figma, и так далее.",
        "Там Excel, Notion и так далее.",
        "glossary terms the speaker never said are not written into the text",
    ),
    (
        "А, и у меня был worktree для сборки.",
        "А, и у меня был new worktree для сборки.",
        "А, и у меня был worktree для сборки.",
        "a term is not grown into a longer one out of nothing",
    ),
    (
        "Моя задача получить митинги для клиента для медмаркета компании.",
        "Моя задача — получить митинги для клиента, для Acme Motors marketplace компании.",
        "Моя задача — получить митинги для клиента, для медмаркета компании.",
        "an invented term does not get through",
    ),
    (
        "давай.",
        "Давай.",
        "Давай.",
        "a short phrase does not break",
    ),
    (
        "Это для клейма Mailwing. Я его сейчас сам заполняю.",
        "Это для клейма Mailflow. Я его сейчас сам заполняю.",
        "Это для клейма Mailwing. Я его сейчас сам заполняю.",
        "one glossary term must not be swapped for another",
    ),
    (
        "Нам не обязательно ехать в тот же магазин.",
        "Нам не обязательно ехать в тот же shop.",
        "Нам не обязательно ехать в тот же магазин.",
        "an ordinary word must not be translated into a term",
    ),
    (
        "только что 8 окон github.exe открылось",
        "Только что 8 окон GitHub открылось.",
        "Только что 8 окон GitHub.exe открылось.",
        "restoring a dropped word must not lose the dot in github.exe",
    ),
    (
        "Опять 20 окон гитхаба сейчас летает.",
        "Опять 20 окон GitHub сейчас летает.",
        "Опять 20 окон GitHub сейчас летает.",
        "heard in Cyrillic -> a Latin-script term: this is allowed",
    ),
    (
        "Я хочу PostHog отчеты делать.",
        "Я хочу делать PostHog отчёты.",
        "Я хочу PostHog отчёты делать.",
        "a dropped last word comes back without a stray dot in the middle",
    ),
    (
        "Собери три штуки",
        "Собери три штуки.",
        "Собери три штуки.",
        "the corrector may add a full stop at the end",
    ),
    (
        "Чтобы я мог заранее прикинуть, сколько влезет в коробку.",
        "Чтобы я мог заранее прикинуть, сколько влезет в коробку?",
        "Чтобы я мог заранее прикинуть, сколько влезет в коробку.",
        "a subordinate clause is the tail of a thought, not a question",
    ),
    (
        "Чтобы что.",
        "Чтобы что?",
        "Чтобы что?",
        "the short retort is a real question: the mark stays",
    ),
    (
        "Чтобы ты понял?",
        "Чтобы ты понял?",
        "Чтобы ты понял?",
        "a short subordinate clause is left alone: it can be a retort",
    ),
    (
        "Сделай мне CSV. Чтобы я мог посмотреть, что там вышло.",
        "Сделай мне CSV. Чтобы я мог посмотреть, что там вышло?",
        "Сделай мне CSV. Чтобы я мог посмотреть, что там вышло.",
        "only the second sentence loses its mark; the first is untouched",
    ),
    (
        "Раскладывай их по полкам.",
        "Раскладывай их по полкам?",
        "Раскладывай их по полкам.",
        "an imperative is recognized by morphology, even if it is not in the list",
    ),
    (
        "Сноси эту ветку.",
        "Сноси эту ветку?",
        "Сноси эту ветку.",
        "and another imperative that is also missing from the list",
    ),
    (
        "Можешь мне эту полку подвинуть?",
        "Можешь мне эту полку подвинуть?",
        "Можешь мне эту полку подвинуть?",
        "a modal is not an imperative: a real question is left alone",
    ),
    (
        "Есть какие-то остатки по складам?",
        "Есть какие-то остатки по складам?",
        "Есть какие-то остатки по складам?",
        "the verb to be is not an imperative: a real question is left alone",
    ),
    (
        "Статьи готовы?",
        "Статьи готовы?",
        "Статьи готовы?",
        "a plural noun is not treated as an imperative",
    ),
    (
        "Хорошо, ставь таймер на полив раз в сутки.",
        "Хорошо, ставь таймер на полив раз в сутки?",
        "Хорошо, ставь таймер на полив раз в сутки.",
        "a filler word does not hide the imperative",
    ),
    (
        "Ну ладно, короче, запускай прогон.",
        "Ну ладно, короче, запускай прогон?",
        "Ну ладно, короче, запускай прогон.",
        "several fillers in a row do not hide it either",
    ),
    (
        "Так у тебя есть ключ?",
        "Так у тебя есть ключ?",
        "Так у тебя есть ключ?",
        "no imperative at all: a real question is left alone",
    ),
    # --- an order with a question inside: the mark goes (chosen 2026-08-14) ---
    (
        "Ты мне скажи, какие грядки ты их поливал.",
        "Ты мне скажи, какие грядки ты их поливал?",
        "Ты мне скажи, какие грядки ты их поливал.",
        "imperative before the question word: this is an order",
    ),
    (
        "Ещё раз покажи нам, о каких грядках говорил я.",
        "Ещё раз покажи нам, о каких грядках говорил я?",
        "Ещё раз покажи нам, о каких грядках говорил я.",
        "a noun before the imperative must not hide it",
    ),
    (
        "У нас есть видео про Excel на YouTube, найди его.",
        "У нас есть видео про Excel на YouTube, найди его?",
        "У нас есть видео про Excel на YouTube, найди его.",
        "an imperative at the end counts too",
    ),
    (
        "Какие грядки ты их поливал.",
        "Какие грядки ты их поливал?",
        "Какие грядки ты их поливал?",
        "question word before the imperative: a real question",
    ),
    # --- traps: words that are both an imperative and a past tense ---
    (
        "Деньги пришли?",
        "Деньги пришли?",
        "Деньги пришли?",
        "an ambiguous verb after a subject is past tense, not an imperative",
    ),
    (
        "Все ключи уже пришли?",
        "Все ключи уже пришли?",
        "Все ключи уже пришли?",
        "still a question with a word between the subject and the verb",
    ),
    (
        "Пришли мне ключи?",
        "Пришли мне ключи?",
        "Пришли мне ключи?",
        "an order, but the recognizer heard the mark: reversed 22.08, see rule 5",
    ),
    # --- a mark the corrector invented with nothing to go on ---
    (
        "Что-то прям вообще сегодня тихо стало.",
        "Что-то прям вообще сегодня тихо стало?",
        "Что-то прям вообще сегодня тихо стало.",
        "the recognizer heard no question and there is no question word: dropped",
    ),
    (
        "Принтер снова еле дышит.",
        "Принтер снова еле дышит?",
        "Принтер снова еле дышит.",
        "here too the corrector invented a mark out of nothing",
    ),
    (
        "Мы разгружаем коробки уже?",
        "Мы разгружаем коробки уже?",
        "Мы разгружаем коробки уже?",
        "the recognizer heard the question itself: the mark is trusted",
    ),
    (
        "Почему статьи не залились.",
        "Почему статьи не залились?",
        "Почему статьи не залились?",
        "a question word is present: the corrector mark is accepted",
    ),
    (
        "Кто закрыл доступ Supabase'а, интересно?",
        "Кто закрыл доступ Supabase'a, интересно?",
        "Кто закрыл доступ Supabase'а, интересно?",
        "a Latin letter that looks the same is not a term: the ending stays Cyrillic",
    ),
]


def main() -> None:
    bad = 0
    for said, polished, want, why in CASES:
        got = constrain(said, polished, ALLOWED)
        ok = got == want
        if not ok:
            bad += 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      said:     {said}")
            print(f"      returned: {polished}")
            print(f"      wanted:   {want}")
            print(f"      got:      {got}")
    print(f"\n{len(CASES)-bad} of {len(CASES)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
