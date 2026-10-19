#!/usr/bin/env python3
"""Classify a diff as docs, surface, runtime or engine (issue #1077, ADR-024 §3).

This is the one path list. CI, CD and the Train replay read the type from here
instead of keeping lists of their own. A file is classified by the first rule it
matches, in the order engine, docs, runtime, surface. A file no rule names counts
as engine, so a gap in the list fails safe. A diff takes the strictest type of
its files, and a release's type is the strictest of the changes since the last
deploy, which is the same as classifying the whole range at once.

The engine rule is the ``ENGINE_PATHS`` of ADR-025, unchanged.

Usage:
    python3 scripts/dev/change-type.py BASE HEAD            print the type of the diff
    python3 scripts/dev/change-type.py --files < names      print the type of the names on stdin
    python3 scripts/dev/change-type.py BASE HEAD --at-least runtime
        exit 0 if the type is runtime or stricter
    python3 scripts/dev/change-type.py BASE HEAD --list engine
        print the files of that type, one per line

Exit codes:
    0 - printed a type or a list, or the type met ``--at-least``
    1 - the type is milder than ``--at-least``
    2 - bad arguments or git failed
"""

import re
import subprocess
import sys

TYPES = ("docs", "surface", "runtime", "engine")

ENGINE_PATHS = (
    r"^src/hermes/(domain|application)/"
    r"|^src/hermes/adapters/(fills|fees)/"
    r"|^config/strategies\.yaml$"
    r"|^docs/experiments/[^/]+/(fee-history\.csv|data-manifest\.json)$"
    r"|^uv\.lock$"
)

# Deployed code that is neither engine nor presentation. Config other than
# strategies.yaml lands here because the engine rule above claims that file first.
# ``src/hermes/ports/`` is deliberately unnamed: a port is a contract the engine
# depends on, so it falls through to engine.
RUNTIME_PATHS = (
    r"^src/hermes/[^/]+\.py$"
    r"|^src/hermes/(adapters|infrastructure|entrypoints)/"
    r"|^config/"
    r"|^deploy/"
    r"|^scripts/(deploy|data|backtest)/"
    r"|^\.github/workflows/cd\.yml$"
    r"|^tests/e2e/|^tests/fixtures/e2e/"
    r"|^pyproject\.toml$|^\.python-version$"
)

# What a person sees. It is checked before the runtime rule, because the console
# screens sit inside directories that are otherwise runtime.
SCREEN_PATHS = (
    r"^src/hermes/adapters/console/"
    r"|^src/hermes/entrypoints/(tui[a-z_]*|console|status_view|preview|logs|version)\.py$"
)

# Tests and tooling that ship nothing to a machine.
SURFACE_PATHS = (
    r"^tests/"
    r"|^scripts/dev/|^scripts/assets/|^tools/"
    r"|^\.github/|^\.claude/"
    r"|^\.(gitignore|gitattributes|gitleaksignore|markdownlint-cli2\.jsonc|pre-commit-config\.yaml)$"
    r"|^\.env\.local\.example$|^cosmic-ray[a-z-]*\.toml$"
)

DOCS_PATHS = r"(^|/)[^/]+\.md$|^docs/|^openspec/|^changelog\.d/"

# Order matters: the first match wins. Screens precede ``runtime`` so the console
# beside ``serve.py`` is surface, and ``runtime`` precedes the rest of ``surface``
# so that ``cd.yml`` is runtime inside ``.github/``.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("engine", re.compile(ENGINE_PATHS)),
    ("docs", re.compile(DOCS_PATHS)),
    ("surface", re.compile(SCREEN_PATHS)),
    ("runtime", re.compile(RUNTIME_PATHS)),
    ("surface", re.compile(SURFACE_PATHS)),
)


def classify_file(path: str) -> str:
    """Return the change type of one repository-relative path."""
    for change_type, pattern in _RULES:
        if pattern.search(path):
            return change_type
    return "engine"


def classify(paths: list[str]) -> str:
    """Return the strictest type among ``paths``; an empty diff is docs."""
    found = {classify_file(path) for path in paths}
    return max(found, key=TYPES.index, default="docs")


def _changed_files(base: str, head: str) -> list[str]:
    """Return the names changed between two commits."""
    result = subprocess.run(  # noqa: S603 — fixed argv; revisions come from CI
        ["git", "diff", "--name-only", base, head],  # noqa: S607 — git on PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"change-type: git diff failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    """Run the command line."""
    args = argv[1:]
    at_least: str | None = None
    list_type: str | None = None
    from_stdin = False
    positional: list[str] = []
    it = iter(args)
    for arg in it:
        if arg == "--files":
            from_stdin = True
        elif arg in ("--at-least", "--list"):
            value = next(it, "")
            if value not in TYPES:
                print(f"change-type: {arg} needs one of {', '.join(TYPES)}", file=sys.stderr)
                return 2
            if arg == "--at-least":
                at_least = value
            else:
                list_type = value
        else:
            positional.append(arg)

    if from_stdin and not positional:
        paths = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    elif len(positional) == 2 and not from_stdin:
        paths = _changed_files(positional[0], positional[1])
    else:
        print(__doc__, file=sys.stderr)
        return 2

    if list_type is not None:
        for path in paths:
            if classify_file(path) == list_type:
                print(path)
        return 0
    change_type = classify(paths)
    if at_least is not None:
        return 0 if TYPES.index(change_type) >= TYPES.index(at_least) else 1
    print(change_type)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
