# -*- coding: utf-8 -*-
"""Checks the lock: the corrector must not be able to rewrite the speaker.

    ..\\.venv\\Scripts\\python.exe test_constrain.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import allowed_words, constrain  # noqa: E402

TERMS = ["AcmePass", "GitHub", "Claude Code", "Chrome Extension", "Supabase",
         "Intercom", "loop", "worktree", "Codex", "outbound"]
ALLOWED = allowed_words(TERMS)

# (what was said, what the corrector returned, what must come out, why)
CASES = [
    (
        "И где лежит его доки?",
        "Где лежат его документы?",
        "И где лежит его доки?",
        "do not change words, and do not drop the leading conjunction",
    ),
    (
        "Что за хуйня, только что 8 окон github.exe открылось.",
        "Что за хуёня, только что 8 окон GitHub.exe открылось.",
        "Что за хуйня, только что 8 окон GitHub.exe открылось.",
        "keep the profanity intact, but take GitHub capitalized",
    ),
    (
        "Спланируй новых статей и ссылок на акме паса, причем на главную.",
        "Спланируй новые статьи и ссылки на AcmePass, причём на главную.",
        "Спланируй новых статей и ссылок на AcmePass, причём на главную.",
        "a term may be substituted, grammar may not be edited",
    ),
    (
        "открой chrome extension я тебе открыл github сделай там себе токены",
        "Открой Chrome Extension, я тебе открыл GitHub. Сделай там себе токены.",
        "Открой Chrome Extension, я тебе открыл GitHub. Сделай там себе токены.",
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
        "Иди собери контекст, отправляя агентов по Outbound репозиторию.",
        "anything the model added on its own is dropped",
    ),
    (
        "Моя задача получить митинги для клиента для медмаркета компании.",
        "Моя задача — получить митинги для клиента, для China Cars marketplace компании.",
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
        "Мне не обязательно попадать в ту же сессию.",
        "Мне не обязательно попадать в ту же session.",
        "Мне не обязательно попадать в ту же сессию.",
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
        "Чтобы я мог спотчекнуть, как они все выглядят.",
        "Чтобы я мог спотчекнуть, как они все выглядят?",
        "Чтобы я мог спотчекнуть, как они все выглядят.",
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
        "Переводи их на ажур.",
        "Переводи их на ажур?",
        "Переводи их на ажур.",
        "an imperative is recognized by morphology, even if it is not in the list",
    ),
    (
        "Сноси эту ветку.",
        "Сноси эту ветку?",
        "Сноси эту ветку.",
        "and another imperative that is also missing from the list",
    ),
    (
        "Можешь мне это как-то объяснить?",
        "Можешь мне это как-то объяснить?",
        "Можешь мне это как-то объяснить?",
        "a modal is not an imperative: a real question is left alone",
    ),
    (
        "Есть какие-то данные по машинам?",
        "Есть какие-то данные по машинам?",
        "Есть какие-то данные по машинам?",
        "the verb to be is not an imperative: a real question is left alone",
    ),
    (
        "Статьи готовы?",
        "Статьи готовы?",
        "Статьи готовы?",
        "a plural noun is not treated as an imperative",
    ),
    (
        "Хорошо, ставь CRM в раз в сутки мониторить.",
        "Хорошо, ставь CRM в раз в сутки мониторить?",
        "Хорошо, ставь CRM в раз в сутки мониторить.",
        "a filler word does not hide the imperative",
    ),
    (
        "Ну ладно, короче, запускай прогон.",
        "Ну ладно, короче, запускай прогон?",
        "Ну ладно, короче, запускай прогон.",
        "several fillers in a row do not hide it either",
    ),
    (
        "Так у тебя есть работа?",
        "Так у тебя есть работа?",
        "Так у тебя есть работа?",
        "no imperative at all: a real question is left alone",
    ),
    # --- an order with a question inside: the mark goes (chosen 2026-08-14) ---
    (
        "Ты мне скажи, какие журналы ты их читал.",
        "Ты мне скажи, какие журналы ты их читал?",
        "Ты мне скажи, какие журналы ты их читал.",
        "imperative before the question word: this is an order",
    ),
    (
        "Ещё раз объясни мне, о каких журналах говорил я.",
        "Ещё раз объясни мне, о каких журналах говорил я?",
        "Ещё раз объясни мне, о каких журналах говорил я.",
        "a noun before the imperative must not hide it",
    ),
    (
        "У нас есть ролик про Bitrix на YouTube, найди его.",
        "У нас есть ролик про Bitrix на YouTube, найди его?",
        "У нас есть ролик про Bitrix на YouTube, найди его.",
        "an imperative at the end counts too",
    ),
    (
        "Какие журналы ты их читал.",
        "Какие журналы ты их читал?",
        "Какие журналы ты их читал?",
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
        "Пришли мне ключи.",
        "but with no subject it is an order: the mark goes, even from the recognizer",
    ),
    # --- a mark the corrector invented with nothing to go on ---
    (
        "Что-то прям вообще последнее плохо стало.",
        "Что-то прям вообще последнее плохо стало?",
        "Что-то прям вообще последнее плохо стало.",
        "the recognizer heard no question and there is no question word: dropped",
    ),
    (
        "Интернет опять еле живой.",
        "Интернет опять еле живой?",
        "Интернет опять еле живой.",
        "here too the corrector invented a mark out of nothing",
    ),
    (
        "Мы заливаем статьи уже?",
        "Мы заливаем статьи уже?",
        "Мы заливаем статьи уже?",
        "the recognizer heard the question itself: the mark is trusted",
    ),
    (
        "Почему статьи не залились.",
        "Почему статьи не залились?",
        "Почему статьи не залились?",
        "a question word is present: the corrector mark is accepted",
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
