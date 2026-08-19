# Local dictation

Hold a key, speak, and the text appears wherever your cursor is. Everything runs
on your own machine: no internet, no subscription, no audio leaving the computer.

Built for Russian speech that is full of English product names — the kind of
dictation where "кложд код" has to come out as `Claude Code` and "луп" as `loop`.

```
 hold F13  →  speak  →  release  →  text is pasted, ~0.3 s later
```

## What it is made of

| Stage | What runs | Why |
|---|---|---|
| Recognition | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `large-v3-turbo`, CUDA, float16, beam 5 | On 383 real takes turbo was both more accurate than large-v3 (17.4% vs 19.2% divergence from a reference transcript) and 4× faster |
| Replacements | `fixes.tsv`, applied instantly | "heard → correct". Grows on its own: when the corrector fixes the same word twice, it is remembered forever |
| Corrector | any chat model in [LM Studio](https://lmstudio.ai) | Puts punctuation and capitals back, writes your terms correctly, restores question marks |
| The lock | `stt/polish.py::constrain` | The corrector is somebody else's model. Its answer is never taken whole — see below |
| Pasting | clipboard + Ctrl+V | Typing Cyrillic character by character is slow and breaks in terminals |

The corrector is optional. If LM Studio is not running, dictation still works and
gives you the raw recognized text.

## The lock — the part that matters most

A general-purpose model, told to "fix the transcript", will quietly rewrite you.
Measured, real failures: it turned "доки" into "документы", "надо" into "нужно",
mangled profanity, swapped one glossary term for another, and translated ordinary
Russian words into English terms.

So `constrain()` compares the model's answer with what you actually said, word by
word, and accepts only four things:

* punctuation,
* capital letters,
* replacing a word the recognizer wrote phonetically with a glossary term,
* flipping a verb between "I will do" and "do it" — from a known list only.

Everything else is rolled back to your words. 37 test cases in
`bench/test_constrain.py`, every one of them from a real failure.

## Question marks

In speech, a statement and a question are often the same words — only the
intonation differs, and the recognizer does not hear it. This was measured
properly on 93 single-sentence takes against an independent reference:

| policy | questions caught | false marks | precision |
|---|---|---|---|
| recognizer alone | 33 / 43 | 1 | 97% |
| corrector, unrestricted | 41 / 43 | 7 | 85% |
| + no question mark on commands | 37 / 43 | 3 | 92% |
| **+ mark must be supported** | **36 / 43** | **1** | **97%** |

"Supported" means: the recognizer heard a question mark itself, **or** the
sentence contains a question word. A mark the corrector invented out of nothing
is dropped. A question mark on an order is dropped too — an agent reading
"Count them, how many are there?" asks back instead of doing the work.

Voice-based detection was tried twice and does not work: the best prosodic
feature separates the two classes at 0.58 where 0.8 is needed.

## Requirements

* Windows (uses Win32 hotkeys, clipboard and per-app audio volume)
* NVIDIA GPU for CUDA (falls back to CPU automatically, just slower)
* Python 3.11+
* [LM Studio](https://lmstudio.ai) with any chat model — optional

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install faster-whisper sounddevice soxr numpy httpx keyboard pyperclip pymorphy3 pycaw

copy glossary.example.txt glossary.txt
copy fixes.example.tsv    fixes.tsv
copy mywords.example.txt  mywords.txt

.\start-background.ps1          # run detached
.\run.ps1                       # or in a window, with the log on screen
```

Everything is configured in `config.toml`; every setting has a comment saying
what it does and why it is set that way.

## Keys

| | |
|---|---|
| **F13** hold | records while held |
| **F13** tap | hands-free: records until the next press |
| **Esc** | cancel the recording |
| **Shift+F13** | edit the last take; the correction is remembered forever |
| **Ctrl+F13** | "that was a question": flips `.` ↔ `?` at the end, in the text and in the window you already pasted into |
| **mouse Forward** | paste the last take into the window under the cursor |

## The page

`http://127.0.0.1:8756` — starts with the app, local only.

Three tabs: the **feed** of every take (click to edit, mark bad, listen),
the **dictionary** (terms, replacements, protected words) and the **model**
picker, which lists whatever is loaded in LM Studio and lets you switch the
corrector without restarting.

## Tests

No microphone, no GPU and no LM Studio needed for most of them — the hardware is
faked.

```powershell
cd bench
..\.venv\Scripts\python.exe test_constrain.py    # the lock, 37 cases
..\.venv\Scripts\python.exe test_endings.py      # order vs. promise, 15
..\.venv\Scripts\python.exe test_fixes.py        # replacements dictionary, 12
..\.venv\Scripts\python.exe test_flip.py         # the question-mark key, 10
..\.venv\Scripts\python.exe test_duck.py         # ducking other audio, 13
..\.venv\Scripts\python.exe test_tail.py         # recording the tail, 5
..\.venv\Scripts\python.exe test_gpu_recover.py  # surviving a lost GPU, 9
..\.venv\Scripts\python.exe test_models_api.py   # model picker, 12
..\.venv\Scripts\python.exe test_api.py          # the page, 15 (needs the app running)
```

## Privacy

The app stores every recording and transcript on disk, in `recordings/` and
`logs/`. `.gitignore` keeps those, your dictionaries and the benchmark outputs
out of git. Check `git status` before you publish anything.

## Notes

Comments in the source are in Russian: they carry the reasoning and the measured
numbers behind each decision, and they were written as the tool was built against
real speech. The user-facing page and this file are in English.

## License

MIT
