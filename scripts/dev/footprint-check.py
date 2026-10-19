#!/usr/bin/env python3
"""Keep outside project names and the bot's account name out of hermes (#1198).

AGENTS.md rule 11 and ``docs/guides/dev-workflow.md`` § Zero-footprint policy
keep outside projects out of every hermes artifact, except the dated
prior-art carve-out: ``docs/research/`` and accepted ADRs under
``docs/architecture/``. The names to look for live inside that carve-out, in
``docs/research/prior-art-names.txt``, so this check names none itself.

Fails when:

- a tracked file outside the carve-out and the release notes contains a
  listed name as a whole word (any case);
- a Markdown file names the bot's account (``rafo-claude-bot``) in prose. In
  prose the bot is ``ai-bot``; the account name may appear only in code
  (inline code or a fenced block), where it is load-bearing.

Usage:
    python scripts/dev/footprint-check.py

Exit codes:
    0  no footprint found
    1  a footprint was found (printed one per line)
"""

import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

NAMES_FILE = Path("docs/research/prior-art-names.txt")
BOT_ACCOUNT = "rafo-claude-bot"
# The carve-out, plus release notes that record history as it shipped.
NOT_SCANNED = ("docs/research/", "docs/architecture/", "CHANGELOG.md", "changelog.d/")

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`]+`")


def _run(tool: str, *args: str) -> str:
    """Run ``tool`` with ``args`` and return its standard output."""
    executable = shutil.which(tool)
    if executable is None:
        raise FileNotFoundError(f"{tool} is not on PATH")
    return subprocess.run(  # noqa: S603 — fixed argv; the tool is resolved via shutil.which
        [executable, *args], capture_output=True, text=True, check=True
    ).stdout


def load_names(text: str) -> list[str]:
    """Return the names in a names file: one per line, ``#`` starts a comment."""
    names = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [name for name in names if name]


def name_pattern(names: Iterable[str]) -> re.Pattern[str]:
    """Return one case-insensitive whole-word pattern for ``names``."""
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def name_hits(text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return ``(line number, name)`` for each listed name in ``text``."""
    return [
        (number, match.group(0))
        for number, line in enumerate(text.splitlines(), start=1)
        for match in pattern.finditer(line)
    ]


def _blank(match: re.Match[str]) -> str:
    """Return ``match`` with every character but newlines turned into spaces."""
    return re.sub(r"[^\n]", " ", match.group(0))


def bot_prose_lines(markdown: str) -> list[int]:
    """Return the lines where the bot's account name appears outside code.

    Fenced blocks and inline code spans, including spans that wrap onto the
    next line, are blanked first, so only prose is searched.
    """
    kept: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            kept.append("")
            continue
        kept.append("" if in_fence else line)
    prose = _INLINE_CODE.sub(_blank, "\n".join(kept))
    return sorted(
        {prose.count("\n", 0, match.start()) + 1 for match in re.finditer(BOT_ACCOUNT, prose)}
    )


def tracked_files(root: Path) -> list[Path]:
    """Return every tracked file outside ``NOT_SCANNED``."""
    listed = _run("git", "-C", str(root), "ls-files", "-z").split("\0")
    return [root / name for name in listed if name and not name.startswith(NOT_SCANNED)]


def footprint_problems(root: Path, files: Iterable[Path], names: Iterable[str]) -> list[str]:
    """Return one message per footprint in ``files``."""
    pattern = name_pattern(names)
    problems: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        where = path.relative_to(root)
        problems.extend(
            f"{where}:{number}: names an outside project ({name}); describe the pattern "
            "instead, or move the citation into docs/research/"
            for number, name in name_hits(text, pattern)
        )
        if path.suffix == ".md":
            problems.extend(
                f"{where}:{number}: the bot is `ai-bot` in prose; its account name "
                "belongs only in code"
                for number in bot_prose_lines(text)
            )
    return problems


def main() -> int:
    """Check every tracked file outside the carve-out."""
    root = Path(_run("git", "rev-parse", "--show-toplevel").strip())
    names = load_names((root / NAMES_FILE).read_text(encoding="utf-8"))
    problems = footprint_problems(root, tracked_files(root), names)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
