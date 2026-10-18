#!/usr/bin/env python3
"""Write the ADR index table in docs/architecture/README.md (issue #1197).

Reads every ``docs/architecture/adr-NNN-*.md`` file, and the ``README.md``
of every ``adr-NNN-*/`` folder, and writes one row per ADR between the
index markers: number, title, status and what supersedes it. The table
depends on file content only, so the same ADRs always give the same table.

The status column is the first word of the ``**Status**:`` line
(``Accepted``, ``Proposed``, ``Superseded``). The last column joins every
``**Superseded by**:`` and ``**Superseded in part by**:`` line (with its
continuation lines), or shows a dash.

Usage:
    python3 scripts/dev/regenerate-adr-index.py          rewrite the table
    python3 scripts/dev/regenerate-adr-index.py --check  exit 1 if the
                                                         committed table differs
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "docs" / "architecture"
START = "<!-- adr-index-start -->"
END = "<!-- adr-index-end -->"

_ADR = re.compile(r"^adr-(\d{3})-[a-z0-9-]+(\.md)?$")
_TITLE = re.compile(r"^# ADR-(\d{3}): (.+)$", re.MULTILINE)
_STATUS = re.compile(r"^\*\*Status\*\*: *(\w+)", re.MULTILINE)
_SUPERSEDED = re.compile(
    r"^\*\*Superseded(?: in part)? by\*\*: *(.+(?:\n(?!\*\*|---|\n).+)*)", re.MULTILINE
)


@dataclass(frozen=True)
class Adr:
    """One row of the index."""

    number: str
    title: str
    link: str
    status: str
    superseded_by: str


def _source(entry: Path) -> Path:
    return entry / "README.md" if entry.is_dir() else entry


def read_adrs(folder: Path = ARCHITECTURE) -> list[Adr]:
    """Return every ADR under ``folder``, by number.

    Raises:
        ValueError: An ADR has no ``# ADR-NNN:`` title or ``**Status**:``
            line, or its title number differs from its file name.
    """
    adrs: list[Adr] = []
    for entry in sorted(folder.iterdir()):
        named = _ADR.match(entry.name)
        if named is None or (entry.is_file() and named.group(2) is None):
            continue
        text = _source(entry).read_text(encoding="utf-8")
        title = _TITLE.search(text)
        status = _STATUS.search(text)
        if title is None or status is None:
            raise ValueError(f"{entry.name}: needs a '# ADR-NNN: title' and a '**Status**:' line")
        if title.group(1) != named.group(1):
            raise ValueError(f"{entry.name}: title says ADR-{title.group(1)}")
        superseded = "; ".join(" ".join(m.split()) for m in _SUPERSEDED.findall(text)) or "—"
        link = f"{entry.name}/" if entry.is_dir() else entry.name
        adrs.append(Adr(named.group(1), title.group(2).strip(), link, status.group(1), superseded))
    return adrs


def table(adrs: list[Adr]) -> str:
    """Return the Markdown table for ``adrs``."""
    rows = [
        "| ADR | Title | Status | Superseded by |",
        "| --- | ----- | ------ | ------------- |",
    ]
    rows += [
        f"| [{a.number}]({a.link}) | {a.title.replace('|', '\\|')} | {a.status} | "
        f"{a.superseded_by.replace('|', '\\|')} |"
        for a in adrs
    ]
    return "\n".join(rows)


def render(readme: str, adrs: list[Adr]) -> str:
    """Return ``readme`` with the table between the markers replaced.

    Raises:
        ValueError: The markers are missing or out of order.
    """
    start, end = readme.find(START), readme.find(END)
    if start < 0 or end < start:
        raise ValueError(f"README needs {START} before {END}")
    return f"{readme[: start + len(START)]}\n{table(adrs)}\n{readme[end:]}"


def main(argv: list[str]) -> int:
    """Rewrite or check the index; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the table is stale")
    args = parser.parse_args(argv)
    readme_path = ARCHITECTURE / "README.md"
    current = readme_path.read_text(encoding="utf-8")
    wanted = render(current, read_adrs(ARCHITECTURE))
    if args.check:
        if wanted != current:
            print("docs/architecture/README.md is stale: run scripts/dev/regenerate-adr-index.py")
            return 1
        return 0
    readme_path.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
