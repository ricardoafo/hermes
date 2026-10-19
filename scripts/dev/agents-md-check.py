#!/usr/bin/env python3
"""Keep AGENTS.md short and free of stale ADR ranges (#1195).

AGENTS.md loads into every agent session, so each line costs tokens on every
run. It holds the rules and links; the reasons live in
``docs/decisions/2026-10-18-agents-md-rationale.md``. This check fails when:

- AGENTS.md is longer than ``LINE_BUDGET`` lines;
- it names a fixed ADR range such as "ADRs 001-010", which went stale the
  day ADR-011 was accepted. Point at ``docs/architecture/`` instead.

Usage:
    python scripts/dev/agents-md-check.py [PATH]

Exit codes:
    0  within budget and no ADR range
    1  a problem was found (printed one per line)
"""

import re
import sys
from pathlib import Path

LINE_BUDGET = 300
DEFAULT_PATH = Path("AGENTS.md")

_ADR_RANGE = re.compile(
    r"\bADRs?[\s-]*\d+\s*(?:-|\u2013|\u2014|to)\s*(?:ADR[\s-]*)?\d+\b", re.IGNORECASE
)


def agents_md_problems(text: str, budget: int = LINE_BUDGET) -> list[str]:
    """Return one message per problem in an AGENTS.md text."""
    problems: list[str] = []
    lines = text.splitlines()
    if len(lines) > budget:
        problems.append(
            f"AGENTS.md has {len(lines)} lines, over the {budget}-line budget; move the "
            "reasons to docs/decisions/2026-10-18-agents-md-rationale.md"
        )
    problems.extend(
        f"AGENTS.md:{number}: names the ADR range '{match.group(0)}'; link "
        "docs/architecture/ instead"
        for number, line in enumerate(lines, start=1)
        for match in _ADR_RANGE.finditer(line)
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check AGENTS.md, or the path given."""
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_PATH
    problems = agents_md_problems(path.read_text(encoding="utf-8"))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
