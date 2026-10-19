#!/usr/bin/env python3
"""Ratchets that stop duplication, oversized modules and dead code from coming back.

The repo audit of 18 Oct found each of these classes of mistake (#1201). Each
check compares the tree against a baseline that lists what existed when the
check was adopted, so old cases are reported but don't fail, and a new one
does:

- ``dup``: a 10-line normalised window shared by two ``src/`` modules. The
  baseline lists file pairs; a new pair fails.
- ``size``: a ``src/`` module over 800 lines. The baseline lists each such
  module with its line count; a new one fails, and a listed one fails when it
  grows past its count. Splitting a file below 800 lines leaves a stale line.
- ``deadcode``: vulture at 60% confidence over ``src/hermes`` (ports are
  protocols, so they are skipped), and any ``src/hermes`` module that no
  module in ``src/`` or ``scripts/`` imports. Subcommand modules are found by
  discovery, so ``entrypoints/commands/`` is skipped too.

Baselines sit beside this script: ``ratchets-dup.baseline``,
``ratchets-size.baseline`` and ``ratchets-deadcode.baseline``. A stale
baseline line (the case it names is gone) also fails, so a baseline only
shrinks. ``--write-baseline`` rewrites one from the current tree; a reviewer
sees the diff. Complexity is not here: ruff ``C901`` enforces it.

Called by:
  - ``bash scripts/dev/ci-checks.sh guards`` (dup, size) and ``deadcode``
  - .pre-commit-config.yaml ``ratchets`` and ``ratchets-self-test`` hooks
  - Manually: ``uv run python scripts/dev/ratchets.py dup|size|deadcode``

Env vars:
  HERMES_SELF_TEST  set to 1 to run built-in pass/fail fixtures and exit

Exit codes:
  0  no case outside the baseline and no stale line (or self-test passed)
  1  a new case, a baselined module that grew, or a stale baseline line
  2  usage error, or a self-test failed
"""

import ast
import hashlib
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = "src"
PACKAGE = "src/hermes"
SKIPPED_PACKAGES = ("src/hermes/ports/", "src/hermes/entrypoints/commands/")
WINDOW = 10
MAX_LINES = 800
VULTURE_CONFIDENCE = 60
TRIVIAL_LINES = frozenset({")", "]", "}", "),", "else:", "try:", "return"})
VULTURE_LINE = re.compile(r"^(?P<path>[^:]+):\d+: (?P<what>.+) \(\d+% confidence\)$")


def _baseline_path(check: str) -> Path:
    return Path(__file__).with_name(f"ratchets-{check}.baseline")


def _tracked(prefix: str) -> list[str]:
    listed = subprocess.run(  # noqa: S603 — fixed argv
        ["git", "ls-files", "-z", prefix],  # noqa: S607 — git on PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(p for p in listed.stdout.split("\0") if p.endswith(".py"))


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def duplicate_pairs(sources: dict[str, str]) -> set[str]:
    """Return ``a <-> b`` for each pair of files sharing a normalised window."""
    owners: dict[str, set[str]] = defaultdict(set)
    for path, text in sources.items():
        kept = [
            line
            for line in (raw.strip() for raw in text.splitlines())
            if line
            and not line.startswith(("#", "import ", "from ", '"""'))
            and line not in TRIVIAL_LINES
        ]
        for start in range(len(kept) - WINDOW):
            chunk = "\n".join(kept[start : start + WINDOW])
            owners[hashlib.sha256(chunk.encode()).hexdigest()].add(path)
    pairs: set[str] = set()
    for files in owners.values():
        ordered = sorted(files)
        pairs.update(f"{a} <-> {b}" for i, a in enumerate(ordered) for b in ordered[i + 1 :])
    return pairs


def oversized(sources: dict[str, str]) -> dict[str, int]:
    """Return each file over ``MAX_LINES`` with its line count."""
    sizes = {path: len(text.splitlines()) for path, text in sources.items()}
    return {path: n for path, n in sizes.items() if n > MAX_LINES}


def size_problems(found: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Return one line per new oversized file, grown file or stale entry."""
    problems: list[str] = []
    for path, lines in sorted(found.items()):
        allowed = baseline.get(path)
        if allowed is None:
            problems.append(f"{path}: {lines} lines, over {MAX_LINES}; split it")
        elif lines > allowed:
            problems.append(f"{path}: grew to {lines} lines past its baseline {allowed}; split it")
    problems.extend(
        f"stale baseline entry, remove it: {path} {baseline[path]}"
        for path in sorted(baseline.keys() - found.keys())
    )
    return problems


def _module_name(path: str) -> str:
    return ".".join(Path(path).relative_to(SRC).with_suffix("").parts)


def imported_names(path: str, text: str) -> set[str]:
    """Return every dotted name ``text`` imports, relative imports resolved."""
    parts = Path(path).with_suffix("").parts
    package = list(parts[parts.index("hermes") : -1]) if "hermes" in parts else []
    names: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def orphan_modules(modules: Iterable[str], imported: set[str]) -> set[str]:
    """Return the module names in ``modules`` that nothing in ``imported`` reaches."""
    return {
        module
        for module in modules
        if not any(name == module or name.startswith(f"{module}.") for name in imported)
    }


def _vulture_findings() -> set[str]:
    excluded = ",".join(p.rstrip("/") for p in SKIPPED_PACKAGES)
    result = subprocess.run(  # noqa: S603 — fixed argv
        [
            sys.executable,
            "-m",
            "vulture",
            PACKAGE,
            "--min-confidence",
            str(VULTURE_CONFIDENCE),
            "--exclude",
            excluded,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 3):
        raise RuntimeError(f"vulture failed: {result.stderr.strip()}")
    findings: set[str] = set()
    for line in result.stdout.splitlines():
        match = VULTURE_LINE.match(line.strip())
        if match:
            findings.add(f"{match['path']}: {match['what']}")
    return findings


def _found(check: str) -> set[str] | dict[str, int]:
    if check == "dup":
        return duplicate_pairs({p: _read(p) for p in _tracked(SRC)})
    if check == "size":
        return oversized({p: _read(p) for p in _tracked(SRC)})
    package = [p for p in _tracked(PACKAGE) if not p.startswith(SKIPPED_PACKAGES)]
    modules = {_module_name(p) for p in package if Path(p).stem not in ("__init__", "__main__")}
    imported: set[str] = set()
    for path in _tracked(SRC) + _tracked("scripts"):
        imported |= imported_names(path, _read(path))
    orphans = {
        f"{m}: no module in src/ or scripts/ imports it" for m in orphan_modules(modules, imported)
    }
    return _vulture_findings() | orphans


def _load(check: str) -> list[str]:
    path = _baseline_path(check)
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def set_problems(found: set[str], baseline: set[str]) -> list[str]:
    """Return one line per case outside the baseline, then per stale entry."""
    new = [f"new: {case}" for case in sorted(found - baseline)]
    stale = [f"stale baseline entry, remove it: {case}" for case in sorted(baseline - found)]
    return new + stale


HEADERS = {
    "dup": "# File pairs sharing a 10-line block (#1201). This list only shrinks.\n",
    "size": "# src/ modules over 800 lines, with their line count (#1201). Only shrinks.\n",
    "deadcode": "# Unused code and unimported modules (#1201). This list only shrinks.\n",
}


def run(check: str, *, write: bool) -> int:
    """Run one check against its baseline, or rewrite the baseline."""
    found = _found(check)
    if write:
        lines = (
            [f"{p} {n}" for p, n in sorted(found.items())]
            if isinstance(found, dict)
            else sorted(found)
        )
        body = HEADERS[check] + "".join(f"{line}\n" for line in lines)
        _baseline_path(check).write_text(body, encoding="utf-8", newline="\n")
        print(f"wrote {len(lines)} entries to {_baseline_path(check).name}")
        return 0
    if isinstance(found, dict):
        baseline = {}
        for line in _load(check):
            path, _, count = line.rpartition(" ")
            baseline[path] = int(count)
        problems = size_problems(found, baseline)
    else:
        problems = set_problems(found, set(_load(check)))
    for line in problems:
        print(f"ratchets {check}: {line}", file=sys.stderr)
    if problems:
        print(
            f"\nFix the code, or for a fixed case drop its line from "
            f"{_baseline_path(check).name} (--write-baseline rewrites it).",
            file=sys.stderr,
        )
        return 1
    print(f"ratchets {check}: ok")
    return 0


def _self_test() -> int:
    block = "".join(f"value_{i} = compute({i})\n" for i in range(WINDOW + 1))
    other = "".join(f"other_{i} = compute({i})\n" for i in range(WINDOW + 1))
    cases: list[tuple[str, Callable[[], object], object]] = [
        (
            "a shared block makes a pair",
            lambda: duplicate_pairs({"a.py": block, "b.py": block}),
            {"a.py <-> b.py"},
        ),
        (
            "distinct files make no pair",
            lambda: duplicate_pairs({"a.py": block, "b.py": other}),
            set(),
        ),
        (
            "imports and comments are not compared",
            lambda: duplicate_pairs({"a.py": "import x\n" * 20, "b.py": "import x\n" * 20}),
            set(),
        ),
        ("a file at the limit is fine", lambda: oversized({"a.py": "x\n" * MAX_LINES}), {}),
        (
            "a file over the limit is found",
            lambda: oversized({"a.py": "x\n" * (MAX_LINES + 1)}),
            {"a.py": MAX_LINES + 1},
        ),
        (
            "a baselined file that shrank is fine",
            lambda: size_problems({"a.py": 900}, {"a.py": 950}),
            [],
        ),
        (
            "a baselined file that grew fails",
            lambda: len(size_problems({"a.py": 960}, {"a.py": 950})),
            1,
        ),
        ("a new oversized file fails", lambda: len(size_problems({"a.py": 900}, {})), 1),
        (
            "a file split below the limit leaves a stale line",
            lambda: len(size_problems({}, {"a.py": 950})),
            1,
        ),
        ("a known case passes", lambda: set_problems({"x"}, {"x"}), []),
        ("a new case fails", lambda: set_problems({"x", "y"}, {"x"}), ["new: y"]),
        (
            "a fixed case leaves a stale line",
            lambda: set_problems(set(), {"x"}),
            ["stale baseline entry, remove it: x"],
        ),
        (
            "relative imports resolve to the package",
            lambda: (
                "hermes.domain.lots.fifo"
                in imported_names(
                    "src/hermes/domain/lots/selection.py", "from .fifo import consume\n"
                )
            ),
            True,
        ),
        (
            "an imported module is not an orphan",
            lambda: orphan_modules({"hermes.a", "hermes.b"}, {"hermes.a.thing"}),
            {"hermes.b"},
        ),
    ]
    failures = 0
    for name, call, want in cases:
        got = call()
        if got != want:
            failures += 1
            print(f"SELF-TEST FAIL: {name}: expected {want!r}, got {got!r}")
    if failures:
        return 2
    print(f"SELF-TEST PASS: ratchets.py handles {len(cases)} fixtures.")
    return 0


def main(argv: list[str]) -> int:
    """Run one ratchet: ``dup``, ``size`` or ``deadcode``, optionally ``--write-baseline``."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _self_test()
    write = "--write-baseline" in argv
    checks = [a for a in argv if a != "--write-baseline"]
    if len(checks) != 1 or checks[0] not in HEADERS:
        print("usage: ratchets.py dup|size|deadcode [--write-baseline]", file=sys.stderr)
        return 2
    return run(checks[0], write=write)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
