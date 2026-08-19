# -*- coding: utf-8 -*-
"""Checks the "that was a question" key: full stop <-> question mark.

Why the key exists. In this speaker's voice a question differs from a statement
only by intonation, and the voice carries no usable signal: a measurement on
2026-08-14 over 347 takes caught 2 questions out of 117.

    ..\\.venv\\Scripts\\python.exe test_flip.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stt.polish import flip_question  # noqa: E402

# (what was pasted, what it must become, how many to erase, what to type, why)
CASES = [
    (
        "Скоро это уже закончится.",
        "Скоро это уже закончится?",
        1, "?",
        "full stop becomes a question mark: the main case",
    ),
    (
        "Переводи их на ажур?",
        "Переводи их на ажур.",
        1, ".",
        "and back: the same key removes a false question mark",
    ),
    (
        "И вот опять...",
        "И вот опять?",
        3, "?",
        "three dots are erased as one, otherwise two would be left behind",
    ),
    (
        "И вот опять…",
        "И вот опять?",
        1, "?",
        "a single ellipsis character is one erased character too",
    ),
    (
        "Сделай отчёт!",
        "Сделай отчёт?",
        1, "?",
        "an exclamation mark is flipped as well",
    ),
    (
        "Убери loop",
        "Убери loop?",
        0, "?",
        "no mark at all: erase nothing, just append",
    ),
    (
        "Первое предложение. И второе.",
        "Первое предложение. И второе?",
        1, "?",
        "only the end is flipped; a full stop in the middle is untouched",
    ),
    (
        "",
        "",
        0, "",
        "an empty take breaks nothing",
    ),
    (
        "Что странно.   ",
        "Что странно?   ",
        1, "?",
        "trailing whitespace stays where it was",
    ),
]


def main() -> None:
    bad = 0
    for said, want_text, want_erase, want_type, why in CASES:
        got_text, got_erase, got_type = flip_question(said)
        ok = (got_text, got_erase, got_type) == (want_text, want_erase, want_type)
        bad += 0 if ok else 1
        print(f"[{'v' if ok else 'X'}] {why}")
        if not ok:
            print(f"      was:    {said!r}")
            print(f"      wanted: {want_text!r}, erase {want_erase}, "
                  f"type {want_type!r}")
            print(f"      got:    {got_text!r}, erase {got_erase}, "
                  f"type {got_type!r}")

    # Pressing twice returns the original. Only required for a plain "." or
    # "?": an ellipsis cannot be restored from a single mark.
    roundtrip_bad = 0
    for said, *_rest in CASES:
        tail = said.rstrip()
        if not tail.endswith((".", "?")) or tail.endswith("..."):
            continue
        once, _e, _t = flip_question(said)
        twice, _e, _t = flip_question(once)
        if twice != said:
            roundtrip_bad += 1
            print(f"[X] two presses must restore the original: {said!r} -> {twice!r}")
    bad += roundtrip_bad
    if not roundtrip_bad:
        print("[v] two presses restore the original text")

    print(f"\n{len(CASES)+1-bad} of {len(CASES)+1} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
