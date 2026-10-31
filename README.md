# local-dictation

Push-to-talk dictation for Windows that runs entirely on your own computer. Hold a key (F13 by default; any key can be set in `config.toml`), speak, release, and the text is pasted into the window that has the cursor. Each press and release is one take. Speech is recognized on your graphics card (GPU) by [faster-whisper](https://github.com/SYSTRAN/faster-whisper), an open-source speech recognizer. Then a chat model you already run in [LM Studio](https://lmstudio.ai), a desktop app for local AI models, adds punctuation and fixes product names; this step is called the corrector. A page in your browser, served by your own computer, shows every take. No audio or text leaves the machine, unless you give it an ElevenLabs key: then takes of 20 seconds and more are read by ElevenLabs Scribe, a cloud recognizer, and everything else stays local (see Limits, privacy and what happens on failure).

The tool is built for one job: giving spoken orders to coding agents (Claude Code, Codex) all day, in your own language, full of English product and tool names. Four mistakes that a general dictation tool can ignore matter in this job. A lost first syllable turns an order into a different word. A verb heard as "I will do it" instead of "do it" turns an order into a promise. A question mark invented on an order makes the agent ask back instead of working. A Ctrl+V sent to [herdr](https://herdr.dev), the terminal the agents run in, is ignored. Each of these was measured on real takes and fixed in code; the numbers are below. The tool has been in daily use since August 2026 at about 3,000 takes a fortnight (2,994 in the two weeks to 31 August, `stt/termstats.py`; 9,615 pasted takes between 14 August and 9 October, `stt/asr.py`), and every rule was tuned on that stream.

## What it does

- Records while F13 is held; a short tap records hands-free until the next press; Esc cancels.
- Recognizes speech with faster-whisper `large-v3-turbo` on the GPU. Takes of up to 60 s of speech go to `large-v3`, which hears them better; longer ones stay on turbo.
- Reads long takes (over 25 s by default) with timestamps and puts the hint in front of every 30-second window, so a long take does not lose its last sentence and keeps your terms and punctuation to the end. The pieces are joined without a stray capital where one meets the next.
- Keeps the 500 ms of sound from before the key press (the pre-roll), and keeps recording for up to 700 ms after release until the voice stops, so neither the first nor the last syllable is cut.
- Handles the key the way a hand uses it: a tap starts a hands-free take, and the press that ends it stops at its release however long it is held; a press while the last take is still recording its tail continues that take; a lost key release is noticed. Takes are recognized and pasted in the order they were spoken.
- Ends a hands-free take nobody is speaking in: cancelled after 90 s with no voice, and a take that went quiet after speech is ended and pasted after 120 s. Both are announced on the screen, the cancel for a minute, because nothing records after either and speech that begins late would go to an idle microphone.
- Drops what is not speech instead of pasting it: a quiet take with no speech in it, a recording nobody stopped that ran to the maximum length with a few words from the room, a cough or hum written down as a word, stock subtitle-credit lines, and a dialogue dash the recognizer puts in front of a take. The sound of a dropped take is kept on disk.
- Tidies the text with the corrector, whichever chat model is already loaded in LM Studio: punctuation, capitals, product names. In the default "light" mode, every other change the corrector makes is rolled back. Small repairs follow: a take always opens with a capital, half a pair of quotes the speaker never had is removed, and a "..." that meant "I broke off" is not turned into a full stop.
- Measures itself against what you actually sent: `stt/sentcheck.py` finds each take inside the messages that went out to the coding agent and lists every word you fixed by hand afterwards, and whether the recognizer, the replacement list or the corrector got it wrong.
- Applies a replacement list, `fixes.tsv`, that grows on its own: when the corrector fixes the same mangled word twice, the pair is stored and applied instantly from then on.
- Pastes through the clipboard and Ctrl+V. When the active window belongs to herdr, it sends the text with herdr's own command, `herdr pane send-text`, instead.
- Turns other applications' audio down to 15% while you speak and restores it afterwards.
- Serves a local page with every take (listen, edit, mark bad), the three word lists (`glossary.txt`: names and product terms; `fixes.tsv`: heard-to-correct replacements; `mywords.txt`: your ordinary words the corrector must not turn into English terms), and a picker for the corrector model.
- Keeps every recording with its transcript on disk as material for fine-tuning the recognizer on your voice later; the page shows progress toward 2.5 hours of speech, the target set in `stt/store.py`.

## What was measured

The script or comment that produced each number is named in the last column. The benchmark outputs themselves hold full transcripts and are not published. The hint is a short list of words given to the recognizer before each take; the glossary (`glossary.txt`) is your list of names and product terms.

| Measurement | Result | Where |
|---|---|---|
| Recognizer choice, 383 takes recorded earlier with Spokenly, a cloud dictation app, scored against ElevenLabs Scribe's transcripts of the same audio | `large-v3-turbo` 17.4% word error rate (with the verb hint), more accurate than `large-v3` and 4× faster | `bench/spokenly.py`, `bench/eval.py`, `bench/ab.py` |
| Beam 5 against beam 1 on turbo (the recognizer keeps five candidate readings instead of one), 377 takes | 17.0% against 17.8% word error rate, cost ~0.01 s | `config.toml` |
| Imperative verbs in the recognizer hint, 383 takes | lost orders 4 → 2 (17.0% → 17.4% word error rate); a full-sentence hint lost 1 but scored 18.2% because hint words leaked into transcripts | `stt/asr.py` |
| Second model for short takes, 258 takes under 3 s | models disagreed on 115; a blind comparison with the model names hidden preferred `large-v3` on 32 and turbo on 14; on long takes turbo is 0.27 s against 1.15 s | `config.toml`, `stt/asr.py` |
| The same for takes of 3-20 s, 160 takes (October) | the models wrote different words in 86; three blind judges preferred `large-v3` in 31 and turbo in 11 (44 even): 3-6 s 12 to 1, 6-12 s 11 to 9, 12-20 s 8 to 1; so `short_seconds` went from 3 to 20 | `config.toml` |
| The same for takes of 20-90 s, 60 takes (October), with the long-take settings | different words in 52; three blind judges preferred `large-v3` in 23 and turbo in 10 (19 even): 20-30 s 14 to 4, 30-60 s 9 to 3, over 60 s 0 to 3; so `short_seconds` went from 20 to 60, at the price of a 30 s take being pasted about 1.5 s later | `config.toml` |
| Pre-roll, 2,206 takes over ten days | 68% had no pre-roll; those were re-dictated or marked bad 2.19% of the time against 0.72% | `config.toml` |
| Microphone handed to Slack and Telegram, which barely recorded (Slack not once since 13 August, Telegram for one minute that day), one day | 36 takes (21%) came within two minutes of the previous one and still lost their pre-roll; now a listed app keeps the microphone only while Windows shows it recording | `bench/test_mic_yield_probe.py` |
| Tail after key release | 47% of takes ended mid-sound without a single quiet frame | `config.toml`, `stt/audio.py` |
| Tail limit raised from 400 ms to 700 ms, 1,855 takes (17-30 September) | 24 (1.3%) were cut at the 400 ms limit with the voice still audible; replayed on 479 recordings, a take released in a pause ends about 10 ms later, one released as a word ends 40-60 ms later | `config.toml` |
| Long takes read without timestamps, September | of 136 takes with more than 30 s of speech, 78 lost words (about 1,300 in all); where the two ways differed, twelve blind judges chose timestamps 89 times and the old way 9; cost about 0.2 s, on long takes only | `config.toml`, `stt/asr.py` |
| Hint in front of every 30 s window instead of the first only, 139 long takes | 118 differ; twelve blind judges preferred it 77 to 28 (12 even); a lowercase letter after a full stop 31 times against 10; glossary terms spelled right 148 against 118; the price is the hint read back in a nearly empty last window, 3 takes (2%), caught and re-read the old way | `config.toml`, `stt/asr.py` |
| Penalty on repeated words switched off, 300 takes of 5 s and more | without it 62 takes came out fuller and 30 shorter; three blind judges preferred it 64 to 17; one take looped without it, so a looping read is retried with the penalty | `config.toml`, `stt/asr.py` |
| Re-reading a take whose text is short for its speech, 1,811 takes | would have changed 1 in three weeks, at about 8 ms on every take; left off (`reread_early_end`) | `config.toml` |
| Takes with no speech in the sound as recorded, 9,615 pasted takes (14 August - 9 October) | 9 had none, and all 9 were inventions (subtitle credits, "Music.", "Mmm.", "Yep."); not one was a real phrase | `stt/asr.py` |
| Hands-free takes nobody speaks in | since 10 August, 17 takes of 45 s or more held at most a dozen words; one ran 300 s with all other audio at 15%. Replayed on 103 takes of 45 s or more: cancelling at 90 s loses no real dictation, cancelling at 45 s lost one (its first words came 130 s in) | `config.toml`, `stt/audio.py` |
| What counts as voice for those rules, 6,425 recordings (15.4 hours) | median speech window 0.052 against 0.001 for an idle take; 98.4% of speech windows reach the 0.004 line; the first speech of a take is found 0.74 s in (median) | `stt/audio.py` |
| Recordings nobody stopped, takes of 4 minutes or more (24 August - 10 October) | 10 takes: the 7 real dictations held 47-282 s of speech, the 3 forgotten ones 1-5 s of room talk; the line is 15 s | `stt/asr.py` |
| Corrector-added question marks, replayed over the 177 marks of the narrow exception (10 August - 1 October) | 41 were wrong; the stricter rules take off 24 of those and 2 of 95 correct ones | `config.toml` |
| A take pasted with a small first letter, 1,893 takes (1-10 October) | 18 | `stt/polish.py` |
| A capital where two pieces of a long take meet | 7 of the 8 capitals with no punctuation in front in October were in takes over 30 s | `stt/asr.py` |
| Half a pair of quotes the corrector left after the lock | 7 takes since 29 August (to 2 October) | `stt/polish.py` |
| A broken-off "..." turned by the corrector into a full stop or a question mark | 10 takes in September: 7 became a full stop, 3 a question mark | `stt/polish.py` |
| A cough or hum written as a word and pasted as a whole take | 8 times in two months (to 6 October) | `stt/asr.py` |
| Glossary order, 2,994 takes in a fortnight | four hinted terms were never said; `autopase` was said 116 times and `Lavish` 77 and neither was in the hint | `stt/termstats.py` |
| Checking a stopped LM Studio before every take, 43 takes | median 2,189 ms per take, of which 2,055 ms was the probe | `bench/test_no_stall.py` |
| Corrector cost per take | 0.35 s on a 30B model holding 18 GB; a 4B model holds 3 GB at the same speed | `config.toml` |
| Subtitle-credit phrases ("to be continued", "subtitles by …", in your language) that Whisper learned from its training data and emits on silence | 9 in 18 days; a 238-second recording came back as that phrase alone; four more were stuck on the end of long takes, after real speech (August-September) | `stt/asr.py` |
| Running without a console window | `python.exe` dies when its console is closed, `pythonw.exe` survives | `start-background.ps1` |

## Run it

Requirements: Windows, an NVIDIA GPU for CUDA (falls back to the CPU, slower), Python 3.11+, and LM Studio with any chat model loaded (optional; without it you get the raw recognized text). Video memory: `large-v3-turbo` itself, plus 3 GB for the optional `large-v3` short-take model, plus whatever chat model you keep in LM Studio (3 GB for the default 4B model, 18 GB for a 30B one).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

copy glossary.example.txt glossary.txt
copy fixes.example.tsv    fixes.tsv
copy mywords.example.txt  mywords.txt

.\run.ps1 selftest              # microphone, recognizer, corrector, replacement list
.\start-background.ps1          # run detached; -Restart, -Stop
.\run.ps1                       # or in a window, with the log on screen
.\install-autostart.ps1         # start with Windows; -Remove
```

Every setting lives in `config.toml`; where a value was chosen by measurement, the comment above it says which. Other `run.ps1` commands:

| Command | What it does |
|---|---|
| `run.ps1 mics` | lists microphones |
| `run.ps1 keytest` | finds a key's scan code |
| `run.ps1 bindtoggle` | binds a start/stop button |
| `run.ps1 bench FILE` | times recognition on a WAV |
| `run.ps1 dry FILE...` | runs the whole chain on files without pasting |
| `run.ps1 show ID` | prints everything about one take |
| `run.ps1 learnwords` | rebuilds `mywords.txt` from your transcripts |
| `run.ps1 dashboard` | opens the page |

| Key | Action |
|---|---|
| F13 hold | records while held |
| F13 tap | hands-free: records until the next press |
| Esc | cancels the recording |
| Shift+F13 | opens the last take for editing; the correction is remembered |
| Ctrl+F13 | flips the final mark between "." and "?", in the text and in the window |
| mouse Forward | pastes the last take into the window under the cursor |

The page at `http://127.0.0.1:8756` starts with the app and has three tabs: Feed (every take: click to edit, mark bad, listen), Dictionary (terms, replacements, protected words) and Model (switch the corrector without a restart).

All but four of the 42 `bench/test_*.py` files run without a microphone, GPU or LM Studio; the hardware is faked. Each is a plain script:

```powershell
cd bench
..\.venv\Scripts\python.exe test_constrain.py      # the lock
..\.venv\Scripts\python.exe test_mic_preroll.py    # holding the mic open
..\.venv\Scripts\python.exe test_micgain.py        # holding the mic gain
..\.venv\Scripts\python.exe test_loaded_only.py    # talking only to a loaded model
..\.venv\Scripts\python.exe test_questions_cap.py  # the question-mark cap
..\.venv\Scripts\python.exe test_long_takes.py     # timestamps on long takes
..\.venv\Scripts\python.exe test_key_flow.py       # press, tail, release, paste
..\.venv\Scripts\python.exe test_sentcheck.py      # finding a take in a sent message
```

`test_api.py` needs the app running; `test_person.py`, `test_questions.py` and `test_prompt_person.py` need LM Studio or the GPU.

## How it works

One take passes through these stages in this order. Each stage is a module in `stt/`.

| Stage | Module | What happens |
|---|---|---|
| 1. Record | `audio.py` | Opens the microphone over WDM-KS, the driver-level path that skips Windows sound processing. Between takes a rolling 500 ms buffer is kept, so the pre-roll is already in hand when the key goes down. |
| 2. Recognize | `asr.py` | First a check that there is speech at all: a quiet take with none is dropped, and so is a recording nobody stopped that ran to the maximum length with almost nobody speaking. Then faster-whisper, CUDA, float16, beam 5, with a hint made of imperative verbs plus the 45 glossary terms you say most often. Takes of up to 60 s of speech go to `large-v3`. A take over 25 s is read with timestamps and the hint is repeated in front of every 30 s window; the pieces are joined by `join_pieces()`, which lowers the capital a piece got at a place where the sentence goes on. Stock subtitle lines and a leading dialogue dash are cut off the result. |
| 3. Replace | `fixes.py` | Instant "heard → correct" replacements from `fixes.tsv`. |
| 4. Correct | `polish.py` | Sends the text to the corrector with a 4 s timeout. On any failure the raw text is used. |
| 5. Lock | `polish.py`, `constrain()` | In the default "light" mode, keeps from the corrector's answer only the four kinds of change listed under "The lock on the corrector" and rolls back the rest. Then small repairs: the first letter of the take becomes a capital, broken quote pairs go, a broken-off "..." is given back. |
| 6. Question mark | `asr.py`, `polish.py` | Decides whether the take ends with "?" from what the recognizer heard, what the corrector proposed, and the sound of the ending. |
| 7. Paste | `paste.py` | Clipboard and Ctrl+V, clipboard restored after 1 s; if two takes are pasted closer together than that, your own copy is put back, not the first take's text. In herdr, `herdr pane send-text <pane>`. |
| 8. Learn | `learn.py` | Writes the take to `logs/YYYY-MM-DD.jsonl`, saves audio and text to `recordings/`, and promotes repeated corrector edits into `fixes.tsv`. Later, `sentcheck.py` compares the take with the message that was really sent. |

At start-up, and whenever terms are edited on the page, `termstats.py` re-ranks the glossary by how often each term appears in the last 14 daily logs; the first 4 lines stay on top.

Between takes two background threads keep the microphone ready. The gain thread in `micgain.py` checks the microphone's gain every 2 s, puts it back when another program moves it, and after each take nudges the target toward a peak of 0.5, never outside 34–58 dB. The recorder's keeper thread in `audio.py` asks `_may_hold_mic()` in `__main__.py` whether to keep the microphone: it frees the microphone for a call app from `yield_to` that comes to the front, and keeps it free only while Windows shows that app recording. `miclisteners.py` reads that from the registry (`LastUsedTimeStop = 0`).

### The microphone is read raw and its gain is held

Windows was processing the sound before the app saw it. The author's microphone is a RODE PodMic USB; its RODE Central software installs a sound-processing add-on on the Windows recording device, and the default Windows audio path (MME) delivers 16-bit audio. Measured on the same room noise:

| Path | Room-noise level (1.0 = full scale) | Samples that are exactly zero |
|---|---|---|
| MME 16 kHz (the old default) | 0.000032 | 62% |
| WASAPI shared 48 kHz | 0.000076 | 6% |
| WDM-KS 48 kHz (straight off the device) | 0.000383 | 0.4%, 24-bit |

12 dB and every pause were gone before recognition, and the processing adapts: a 29-second take's level slid from 0.10 to 0.02 of full scale while it was being spoken. So `[mic] path = "raw"` takes WDM-KS. Opening the microphone costs 105 ms on that path, and people start talking as they press; between 26% and 42% of takes used to begin with the first syllable missing. The stream is now held open for `hot_ms` after a take (30 minutes) with the 500 ms rolling buffer. When a call app from `yield_to` comes to the front, the microphone is freed for 2.5 s; if Windows then shows that app recording, it keeps the microphone for the whole call.

The "level" slider in Windows sets the microphone's gain, its built-in amplifier, 22–63 dB, and any program built on WebRTC (Chrome, Zoom, Telegram) moves it and leaves it moved. Once it sat at 63 dB and every take crackled with clipping; two days later it had been dragged to 42.8 dB and speech peaked at 0.13 of full scale. The gain thread now holds the gain and tunes it after every take.

### The corrector only talks to a loaded model

Whatever sits in video memory is your working model, not dictation's. The app asks LM Studio `/api/v0/models`, takes only entries whose `state` is `loaded`, and talks to one of those. It sends no `ttl`, so it cannot start an idle timer on a model it did not load. Swap the model and dictation follows within 20 s; unload everything and dictation pastes the raw text. The obvious endpoint, `/v1/models`, lists every downloaded model, and asking for one of those makes LM Studio load it: that cost 18.5 GB of video memory and 11 s against the 4 s timeout. One exception: every 10 minutes a keep-alive request names the last model seen, without checking again that it is still loaded.

A dead LM Studio is never probed while a take is being handled either. A refused connection to localhost costs 2.0 s on this machine; when the probe ran before every take, the first take after a 30-second pause took 2.1 s instead of 0.15 s. The probe now runs in the background and the take pastes raw text at once.

### The lock on the corrector

A general-purpose model told to "fix the transcript" rewrites what the speaker said. Measured on real takes, it replaced colloquial words with formal synonyms, swapped one glossary term for another, translated ordinary words of the speaker's language into English terms, and inserted product names the speaker never said. Over 2,218 takes, every one of the six terms it inserted was wrong; the worst case added four CRM names to a list of two that ended in "and so on" (`stt/polish.py`). So `constrain()` compares the corrector's answer with what was said, word by word, and accepts four things only:

1. punctuation;
2. capital letters;
3. a glossary term in place of a word the recognizer spelled out phonetically in your language's letters (transliterated similarity of at least 0.55, and never for a word in `mywords.txt`);
4. a verb flipped from first person to the imperative, from the pair list in `stt/endings.py`, in that direction only. Over 2,196 takes the reverse direction broke 4 phrases and fixed none; the forward direction fixed 1.

Everything else is rolled back to the spoken words. `bench/test_constrain.py` holds 39 cases.

### Question marks

In speech a statement and a question are often the same words, and the recognizer writes a full stop. Measured on 93 single-sentence takes (43 questions) against ElevenLabs transcripts:

| Policy | Questions caught | False marks | Precision |
|---|---|---|---|
| recognizer alone | 33 / 43 | 1 | 97% |
| corrector, unrestricted | 41 / 43 | 7 | 85% |
| + no mark on a command | 37 / 43 | 3 | 92% |
| + mark must be supported by a heard mark or a question word | 36 / 43 | 1 | 97% |

That rule still allowed one question word anywhere in the sentence to justify marks everywhere else: over the next 227 takes the corrector added 7 marks and 5 were wrong. So the count is capped: never more marks than the recognizer heard, and anything above that survives only on a sentence that opens as a real question: a question word among the first six words, not somebody else's question passed on ("he asked where the file is"), not an order, not a statement of need ("I need to understand why"). Replayed over the 177 marks of that exception in the logs, 41 were wrong; the stricter rules take off 24 of those and 2 of 95 correct ones. A mark the recognizer heard is never removed, even on a phrase shaped as an order (37 such marks in 12 days of logs, all real questions).

Questions without a question word are the remaining gap, and the recognizer itself closes part of it. `Asr.question_score()` scores the same phrase against the recording twice, once ending in "." and once in "?", and keeps the ending that fits better. Over 421 takes with 148 real questions, this raised delivered questions from 124 to 129 with no extra mark on a statement, at 60 ms per take. Measuring the pitch of the voice was tried first (`bench/pitch.py`) and was too weak to use. Ctrl+F13 flips the final mark by hand, in the text and in the window it was pasted into.

### Long takes

The recognizer reads sound in 30-second windows. Without timestamps it cannot say where it stopped, so whatever it did not finish in one window was skipped and the next window started after it. Over September, 78 of the 136 takes with more than 30 s of speech lost words that way. So a take longer than `timestamps_after_s` (25 s) is read with timestamps; the next window then starts where the words really ended. A second problem sat behind the first: with timestamps on and `condition_on_previous_text` off, faster-whisper gives the hint to the first window only, and the rest of a long take was read without your glossary and without the punctuation style the hint carries. `hint_every_window = true` passes the same hint as hotwords, which go in front of every window; window one reads token for token as before (`bench/test_hint_windows.py` checks that against the installed library). The last window is often a second or two of sound, and given so little to write the recognizer sometimes carries on with the hint; that end is spotted, the take is read once more the old way, and the old text wins unless it ends the same way. Pieces of a long take each start with a capital even when the sentence goes on, so `join_pieces()` lowers it where the piece before has no full stop and the word is an ordinary one, not a name. A looping read (the same word sixty times on a five-minute take) is retried with a penalty on repeated words, and at last read the old way.

### Hands-free takes that nobody speaks in

A tap starts a hands-free take, and a tap nobody meant used to keep recording to the maximum length (one ran 300 s) with every other program turned down to 15%. Now `VoiceWatch` in `audio.py` listens to the take's own sound, with no model: a 0.3 s window counts as voiced at an rms of 0.004 (-48 dBFS), and voice is 3 voiced windows out of the last 4, so a click or a cough is not voice. A take with no voice for 90 s is cancelled; a take that was spoken in and then stayed quiet for 120 s is ended like a normal stop, recognized and pasted. The 120 s stop sits above the longest pause inside a real dictation in the logs (61 s by the speech detector, 81 s by the level rule), and replayed on past takes the 90 s cancel lost no real dictation (a 45 s one lost one). Speech after a cancel or a stop is not recorded, which is why the screen keeps the notice for a minute after a cancel. The same journals showed forgotten takes of four minutes or more that were pasted as if they were speech because a door or a chair kept the voice rule alive: a take that ran into the maximum length with under 15 s of speech is now dropped, and its sound is kept.

### Checking what was really sent

Most dictation here goes into coding-agent windows, and every message sent there is written to a session transcript on disk. `python -m stt.sentcheck --days 7` (from the repository folder, with the virtual environment's Python) reads those transcripts and finds each take inside a message sent within an hour after it: case, punctuation and letter variants are ignored, only words are compared, and typed text before or after the take does not matter. For every word you changed by hand it prints the pasted word, the sent word and who got it wrong: the recognizer, the replacement list or the corrector (or "deleted"). It counts takes sent unchanged, edited by hand, said again, quoted by another window, broken off and said again in the next take, and not found, and it rejects weak matches (a lone shared word, two tiny words, everyday words that turn up in any long text) so that a typed letter is not reported as a correction. This is the honest measure of dictation, "nothing had to be fixed", and the list of what to fix next. It reads local files only.

### Pasting into terminal panes

herdr, the terminal the agents run in, has no paste keybinding, and three Ctrl+V pastes in a row produced nothing while the same code pasted fine into an ordinary window. When the active window belongs to herdr, `paste.py` calls `herdr pane send-text <pane>` for the focused pane, which leaves the clipboard alone. For panes running an agent (Claude Code, Codex) the text is wrapped in bracketed-paste markers, the terminal's way of saying "this is one pasted block"; without them every line break inside a take acted as Enter and each paragraph went off as its own message.

## The improvement loop

The tool is improved continuously from its own daily use. A watcher (`loop/watch.py`) prints every take as it arrives, and every hand correction that `stt/sentcheck.py` finds in the messages that were really sent. Each take is reviewed for misheard terms, broken punctuation, cut-offs and damage done by the corrector. A vocabulary pair goes into `fixes.tsv` only after `loop/check_pair.py` has run it against all past takes. A code fix comes with a bench test on invented phrases, and a replay over past days must show only the intended changes. The procedure is written down in [`loop/README.md`](loop/README.md).

## Limits, privacy and what happens on failure

- Windows only: Win32 hotkeys, the clipboard, WDM-KS audio, per-application volume and the registry are all Windows APIs.
- Written for one language first. `[asr] language` is a setting, but the rule-based clean-up lists (the verb pairs, the question words and the filler words in `stt/endings.py` and `stt/polish.py`, the stock subtitle lines in `stt/asr.py`) and the corrector prompts are written for the author's language and need rewriting for another.
- Long takes can go to the cloud. With an ElevenLabs key (`ELEVENLABS_API_KEY` in the environment, or in the file named by `[cloud] key_file`), every take of `[cloud] min_seconds` (20 s) and more is sent to ElevenLabs Scribe together with your top terms, and its text goes through the same replacement list and corrector. Measured on 200 of the author's takes, judged blind: from 20 s on, 19 errors against 35 for the local recognizer, and the answer sooner (2.1 s against 2.5 s); on shorter takes it was no better and slower, so they stay local. A take during which another program used the microphone (a call) is never sent. No key, no network, an error or a slow answer (`timeout_s` + `timeout_per_s` per second of speech), and the take is read locally. Set `[cloud] enabled = false` to keep everything on the machine.
- Otherwise nothing leaves the machine. The corrector is reached at `http://127.0.0.1:1234`, the page is served on 127.0.0.1 only, and `start-background.ps1` sets `HF_HUB_OFFLINE=1`. The only network use is the one-time download of the Whisper model weights on first run. `[polish] url` is a setting; point it at a remote server and your text goes there.
- The page has no password. Anyone with access to the machine can read every take.
- The raw microphone path is exclusive: while the app holds the microphone (30 minutes after a take) no other program can record, except the call apps in `[mic] yield_to`. Set `path = "shared"` to let every program record at any time and accept the processed sound.
- Everything is stored on disk: `recordings/`, `logs/`, `state/` and the three word lists hold your voice, your words and your colleagues' names. `.gitignore` excludes them, and `push-to-github.ps1` refuses to push if any of them is tracked. Check `git status` before you publish.
- The corrector is optional and never blocks a take: a 4 s timeout, a length check (`max_growth = 1.6`), and the lock all fall back to the raw text.
- If the GPU is lost (after sleep, or when a 30 GB model is loaded next to it), the recognizer drops its second model, reloads on the GPU, and falls back to the CPU if that fails.
- Only one copy runs at a time; a second start exits with a message.
- Fine-tuning is not implemented. The app collects the audio and text pairs and counts them on the page.

## Repository layout

```
stt/                 the app: 21 Python files. The six on a take's path, the glossary ranker, the
                     sent-message check and the two microphone helpers are named above; the rest are
                     the main loop, config, the verb pair list, the on-screen dot, the edit window,
                     the mouse hook, audio ducking, the page server and its store, and CUDA setup.
                     stt/web/index.html is the page.
bench/               42 test_*.py checks (38 of them with faked hardware), plus 16 measurement scripts
                     (eval.py, ab.py, qscore.py, qpolicy.py, why.py, ...) that produced the numbers above
loop/                the improvement loop: the watcher (watch.py) and the vocabulary pair checker
                     (check_pair.py); the procedure is in loop/README.md
config.toml          every setting; the measured ones carry the measurement in a comment
requirements.txt     faster-whisper, sounddevice, soxr, numpy, httpx, keyboard, pyperclip, pymorphy3, pycaw, pynput
run.ps1              run in a window, or one of the helper commands
start-background.ps1 run detached as a console-less process; -Restart, -Stop
install-autostart.ps1 add to Windows startup; -Remove
*.example.*          starter word lists; copy to glossary.txt, fixes.tsv, mywords.txt
```

Comments in `stt/` and `bench/` carry the reasoning and the measured numbers behind each decision.

## License

MIT
