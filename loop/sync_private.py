# -*- coding: utf-8 -*-
"""Copies your own data into a private git repository and pushes it.

The public repository holds the system only. What it learned from you lives
next to it and is never published: the word lists, the journal of every take,
the hand-correction verdicts, handover notes, benchmark inputs. This keeps a
history of them in a repository of your own, which should be PRIVATE.

    python loop/sync_private.py --repo <folder of your private repository>
    python loop/sync_private.py --repo <folder> --no-push    # commit only

The folder must already be a git repository with a remote. The data is copied
into its app-data/ subfolder; anything else in it (your own notes, scripts) is
committed along with it. Recordings are not copied: hours of audio do not
belong in git.
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
# What is yours, relative to the app folder. Everything here is in .gitignore.
PRIVATE = [
    "glossary.txt", "fixes.tsv", "mywords.txt",
    "logs/????-??-??.jsonl", "logs/sent/*.jsonl", "logs/dropped/*.jsonl",
    "HANDOVER*.md",
    "state/*.json",
    "bench/transcripts.txt", "bench/*-pairs*.json", "bench/qeval-dump.json",
    "bench/qlabels.json", "bench/qscore.json", "bench/*-result.json", "bench/*-result.md",
]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def copy_data(repo: Path) -> int:
    dest = repo / "app-data"
    n = 0
    for pattern in PRIVATE:
        for src in sorted(APP.glob(pattern)):
            if not src.is_file():
                continue
            out = dest / src.relative_to(APP)
            if out.exists() and out.stat().st_size == src.stat().st_size \
                    and out.read_bytes() == src.read_bytes():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            n += 1
    return n


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="folder of your private git repository")
    ap.add_argument("--no-push", action="store_true", help="commit, do not push")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"{repo} is not a git repository")
        return 1
    if repo == APP or APP in repo.parents:
        print("the private repository must not sit inside the public one")
        return 1
    changed = copy_data(repo)
    git(repo, "add", "-A")
    if not git(repo, "status", "--porcelain").stdout.strip():
        print(f"nothing new ({changed} files copied)")
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    r = git(repo, "commit", "-m", f"Data as of {stamp}")
    if r.returncode:
        print(r.stdout + r.stderr)
        return 1
    print(r.stdout.splitlines()[0] if r.stdout else "committed")
    if args.no_push:
        return 0
    r = git(repo, "push")
    print((r.stdout + r.stderr).strip() or "pushed")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
