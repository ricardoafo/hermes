#!/usr/bin/env python3
"""Keep outside project names, the bot's account name and private values out of hermes (#1198).

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
  (inline code or a fenced block), where it is load-bearing;
- a tracked file outside ``docs/experiments/`` holds a private value (#1353):
  a value on the scrub list (only its SHA-256 is kept here), a public or
  tailnet IP address, or an AI attribution footer. Examples use the
  documentation range ``203.0.113.0/24``. The signed research record under
  ``docs/experiments/`` stays exactly as it was signed (ADR-014).

Usage:
    python scripts/dev/footprint-check.py

Exit codes:
    0  no footprint found
    1  a footprint was found (printed one per line)
"""

import hashlib
import ipaddress
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Final

NAMES_FILE = Path("docs/research/prior-art-names.txt")
BOT_ACCOUNT = "rafo-claude-bot"
# The carve-out, plus release notes that record history as it shipped.
NOT_SCANNED = ("docs/research/", "docs/architecture/", "CHANGELOG.md", "changelog.d/")

# The signed research record, and the lock file's package hashes.
SCRUB_NOT_SCANNED = ("docs/experiments/", "uv.lock")
# SHA-256 of each value on the scrub list: host addresses, the backup account
# and a personal email address. Only digests live here, so this check names
# none of the values itself.
SCRUBBED_DIGESTS: Final = frozenset(
    {
        "b880c070515231068a4bbbd5be95ea39c5f8a69f07fb835667c2934982264954",
        "262ed9f8eb797d13aa128981f8256e2479c334df87eec01beab14135c434e485",
        "6fd3c1552988bd1d99b58e7c8d52ee42a7da9fd52176685c51a37a75fdd8306b",
        "d7d3a43ec9add71077dfa1747814d3d8c5f09245544e102ea0508257b312a6f4",
        "fe0416d541166131680927b1eee060cfa2731630ba12d03dd5cafc89f4ff1ff3",
    }
)
# Addresses the tests and docs use as obvious fakes or as a range's base.
FAKE_ADDRESSES: Final = frozenset(
    {"1.2.3.4", "100.1.2.3", "100.64.0.0", "100.64.0.1", "100.64.0.2"}
)
_TAILNET: Final = ipaddress.ip_network("100.64.0.0/10")
_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ACCOUNT = re.compile(r"(?<![\w-])u\d{6}(?![\w-])")
_AI_FOOTER = re.compile(
    r"^\s*(?:Co-Authored-By:\s*Claude|\U0001F916 Generated with \[Claude Code\])"
)

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


def tracked_files(root: Path, skip: tuple[str, ...] = NOT_SCANNED) -> list[Path]:
    """Return every tracked file outside the ``skip`` prefixes."""
    listed = _run("git", "-C", str(root), "ls-files", "-z").split("\0")
    return [root / name for name in listed if name and not name.startswith(skip)]


def _digest(value: str) -> str:
    """Return the SHA-256 hex digest of ``value``, the form the scrub list keeps."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_address(candidate: str) -> bool:
    """Return whether ``candidate`` is a public or tailnet IPv4 address that is not a known fake."""
    try:
        address = ipaddress.IPv4Address(candidate)
    except ValueError:
        return False
    return candidate not in FAKE_ADDRESSES and (address.is_global or address in _TAILNET)


def _email_forms(email: str) -> set[str]:
    """Return ``email`` lowercased, with and without a ``+tag`` on its local part."""
    local, _, domain = email.lower().partition("@")
    return {f"{local}@{domain}", f"{local.split('+', 1)[0]}@{domain}"}


def scrub_hits(text: str) -> list[tuple[int, str]]:
    """Return ``(line number, what)`` for each private value in ``text``."""
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        candidates = {match.group(0) for match in _IPV4.finditer(line)}
        candidates |= {match.group(0) for match in _ACCOUNT.finditer(line)}
        for match in _EMAIL.finditer(line):
            candidates |= _email_forms(match.group(0))
        if any(_digest(candidate) in SCRUBBED_DIGESTS for candidate in candidates):
            hits.append((number, "a value on the scrub list"))
        elif any(_public_address(match.group(0)) for match in _IPV4.finditer(line)):
            hits.append((number, "a public or tailnet IP address; use 203.0.113.0/24 in examples"))
        if _AI_FOOTER.match(line):
            hits.append((number, "an AI attribution footer"))
    return hits


def scrub_problems(root: Path, files: Iterable[Path]) -> list[str]:
    """Return one message per private value in ``files``."""
    problems: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        where = path.relative_to(root)
        problems.extend(f"{where}:{number}: holds {what}" for number, what in scrub_hits(text))
    return problems


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
    problems += scrub_problems(root, tracked_files(root, SCRUB_NOT_SCANNED))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
