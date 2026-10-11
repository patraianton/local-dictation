# -*- coding: utf-8 -*-
"""Checks that nothing personal is about to be published.

Run before every push to the public repository (push-to-github.ps1 does). It
reads the owner's private files, which are never published themselves, and
looks for what is in them in every file git would publish (tracked, plus new
files that are not ignored):

  1. the home folder path or the user name;
  2. a person's name from glossary.txt (a word the morphology dictionary
     knows as a first name, surname or patronymic);
  3. a stretch of 6 words or more that is word for word in a real take;
  4. a quoted phrase of 4 words or more that is word for word in a real take.

Only lines that are not published yet are checked (what origin/main does not
have); --all checks every line. Prints every place found and exits with 1;
exits with 0 when clean.

    python privacy_check.py
    python privacy_check.py --all
"""
import argparse
import getpass
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORD_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)
QUOTE_RE = re.compile(r"«([^«»\n]+)»|“([^“”\n]+)”|\"([^\"\n]{8,})\"")
SHINGLE = 6
QUOTED_MIN = 4
TEXT_SUFFIXES = {".py", ".md", ".txt", ".tsv", ".toml", ".ps1", ".html", ".js", ".json",
                 ".css", ".cfg", ".ini", ".yml", ".yaml", ".bat", ".cmd", ""}


def words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in WORD_RE.findall(text or "")]


def published_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    files = [ROOT / p for p in out.split("\0") if p]
    return [f for f in files if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES
            and f.name != Path(__file__).name]


def take_texts() -> list[str]:
    """Every take as heard and as pasted, less the stock phrases the recognizer
    makes up on silence (subtitle credits): those are nobody's words."""
    try:
        sys.path.insert(0, str(ROOT))
        from stt.asr import subtitle_ghost
    except Exception:
        def subtitle_ghost(_text):
            return ""
    texts = []
    for f in sorted((ROOT / "logs").glob("????-??-??.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            for key in ("raw", "final"):
                if r.get(key) and not subtitle_ghost(r[key]):
                    texts.append(r[key])
    return texts


def person_names() -> set[str]:
    path = ROOT / "glossary.txt"
    if not path.exists():
        return set()
    try:
        import pymorphy3
        morph = pymorphy3.MorphAnalyzer()
    except Exception:
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#")[0].strip()
        if not term or " " in term:
            continue
        parses = morph.parse(term)
        if parses and {"Name", "Surn", "Patr"} & set(parses[0].tag.grammemes):
            names.add(term.lower().replace("ё", "е"))
    return names


def published_lines(base: str) -> dict[str, set[str]]:
    """The lines of every file as already published at `base`: those are out
    in the open and not checked again (use --all to see them too)."""
    out = {}
    names = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", base], cwd=ROOT,
                           capture_output=True).stdout.decode("utf-8").split("\0")
    for name in filter(None, names):
        r = subprocess.run(["git", "show", f"{base}:{name}"], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            out[name] = set(r.stdout.decode("utf-8", "replace").splitlines())
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Finds personal data in what would be published.")
    ap.add_argument("--all", action="store_true", help="check every line, published ones too")
    ap.add_argument("--base", default="origin/main",
                    help="what is already published (default origin/main)")
    args = ap.parse_args()
    home = str(Path.home())
    user = getpass.getuser().lower()
    homes = {home.lower(), home.replace("\\", "/").lower(), home.replace("\\", "\\\\").lower()}
    user_re = re.compile(rf"(?<![\w.-]){re.escape(user)}(?![\w-])", re.I) if len(user) > 2 else None

    takes = take_texts()
    joined = " \n " + " \n ".join(" ".join(words(t)) for t in takes) + " \n "
    shingles = set()
    for t in takes:
        w = words(t)
        for i in range(len(w) - SHINGLE + 1):
            shingles.add(tuple(w[i:i + SHINGLE]))
    names = person_names()

    published = {} if args.all else published_lines(args.base)
    found = []
    for f in published_files():
        rel = f.relative_to(ROOT).as_posix()
        old = published.get(rel, set())
        for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line in old:
                continue
            low = line.lower()
            for h in homes:
                if h in low:
                    found.append((rel, f"line {lineno}: home folder path", line.strip()[:160]))
            if user_re and user_re.search(line):
                found.append((rel, f"line {lineno}: user name", line.strip()[:160]))
            w = words(line)
            for name in set(w) & names:
                found.append((rel, f"line {lineno}: person's name from glossary.txt", name))
            for i in range(len(w) - SHINGLE + 1):
                if tuple(w[i:i + SHINGLE]) in shingles:
                    found.append((rel, f"line {lineno}: 6 words from a real take", line.strip()[:160]))
                    break
            for m in QUOTE_RE.finditer(line):
                q = words(next(g for g in m.groups() if g))
                if len(q) >= QUOTED_MIN and f" {' '.join(q)} " in joined:
                    found.append((rel, f"line {lineno}: quote from a real take", m.group(0)[:160]))
    for rel, what, where in found:
        print(f"{rel}: {what}: {where}")
    scope = "every line" if args.all else f"lines not yet in {args.base}"
    print(f"\n{len(published_files())} files ({scope}) checked against {len(takes)} take texts and "
          f"{len(names)} names: {'CLEAN' if not found else f'{len(found)} findings'}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
