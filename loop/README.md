# The improvement loop

The dictation is improved from its own daily use. Someone (a person, or a
coding agent on a schedule) watches every take as it comes in, finds what went
wrong, and fixes the cause: a word list entry, or a change in the code with a
test. This folder holds the tools for that, and this file is the procedure.

## The tools

| Tool | What it does |
|---|---|
| `watch.py` | Runs all day. Prints every take a few seconds after you pause, every take that was dropped, every alarm line in `logs/service.log`, and every hand correction: it finds each take in the messages that were really sent (`stt/sentcheck.py`) and prints `EDITED` with the words you changed by hand. |
| `new_takes.py` | Everything since the last review, in one list; `--mark` records that the review is done. |
| `check_pair.py` | Before a replacement pair goes into `fixes.tsv`: every past take where the wrong form occurs, with the words around it. |
| `relisten.py` | Reads one recording again on the CPU, with both models, or with one live setting off at a time (`--variants`). |
| `wait_idle.py` | Waits until nobody has dictated for N minutes, so a restart cannot cut a take. |
| `../privacy_check.py` | Before anything is published: no home folder path, no name from your glossary, no phrase from your real takes in any file that would be pushed. |
| `sync_private.py` | Copies your own data (word lists, take journals, notes) into a private repository and pushes it there. |

All of them read the app folder they sit in and keep their own state in
`state/`, which is never published.

## One round

1. **Read the takes.** `python loop/new_takes.py` (or the output of
   `watch.py`). For each take, look for a term the recognizer misheard, broken
   punctuation or capitals, a take that was cut off, and anything the corrector
   damaged (compare RAW with FINAL).
2. **Hear it again when in doubt.** `python loop/relisten.py <wav>`. If every
   read on the CPU agrees with the live text, the speaker said it that way:
   leave it. If they agree on something else, find the setting with
   `--variants` before changing anything.
3. **A misheard term gets a pair.** Run `python loop/check_pair.py "wrong form"`
   first. Add the pair only if every past take with that form meant the same
   thing; a form that is a real word somewhere else needs a longer key (two
   words) or no pair. Back up `fixes.tsv`, then append the line. The app reads
   the list again by itself; no restart.
4. **A rule gets code and a test.** Write the case into a `bench/test_*.py`
   with invented phrases (never real dictation: this repository is public),
   make it fail, fix the code, make it pass, and run every other test. Then
   replay the change over past days (for example `python -m stt.sentcheck
   --days 5 --json` before and after) and read every take whose result
   changed: only the intended ones may change.
5. **Restart rarely.** A restart leaves a few seconds in which nothing is
   recorded. Collect code fixes and restart at most once in a few hours, after
   `python loop/wait_idle.py 10`: `.\start-background.ps1 -Restart`, then
   check that `logs/service.log` says READY.
6. **Write it down.** A line in your own journal: what was seen, what was
   changed, what was left alone and why. Then `python loop/new_takes.py --mark`.
7. **Publish.** Code: `python privacy_check.py`, then commit and push. Your
   data: `python loop/sync_private.py --repo <your private repository folder>`.

## What not to do

- Do not add a pair because of one take when the wrong form is also a real
  word: check every past occurrence first.
- Do not put a person's name into `mywords.txt`; that list is for ordinary
  words the corrector must leave alone.
- Do not judge a change by the takes it fixes alone: read the ones it changes
  that were right before.
- Do not publish real dictation, names, or anything from `logs/`,
  `recordings/` or `state/`.
