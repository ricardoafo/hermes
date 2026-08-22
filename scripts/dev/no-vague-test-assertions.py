#!/usr/bin/env python3
"""No-vague-test-assertions AST guard.

Fails any test function whose sole assertion is a vague null-guard
(e.g. `assert x is not None`, `assert result > 0`, `assert x`). Per
`docs/guides/ai-authored-tests.md` §"Failure modes specific to AI-authored
tests" — Assertion inflation.

Called by:
  - .pre-commit-config.yaml `no-vague-test-assertions` hook (production)
  - .pre-commit-config.yaml `no-vague-test-assertions-self-test` hook (self-test)
  - Manually: `uv run python scripts/dev/no-vague-test-assertions.py [PATH...]`

Env vars:
  HERMES_SELF_TEST  — set to 1 to run built-in pass/fail fixtures and exit

Vague assertion classifier:
  - `assert x is None` / `assert x is not None`
  - `assert x > 0` / `assert x >= 0` / `assert x != 0`
  - `assert x == True` / `assert x == False`
  - Bare `assert x` (single Name)
  - `assert x == y` where BOTH sides are plain Names (no Constant AND no
    Attribute anchor). Allowing an Attribute on either side lets meaningful
    domain-value comparisons through — e.g. `assert side == TradeSide.BUY`
    or `assert obj.field == expected_value`. The problematic case that
    survives is `assert result == expected` where the reader cannot tell
    which side is the fixture and which is the system-under-test output.

A test function is VALID if:
  - it has at least one MEANINGFUL assertion (comparison against a Constant
    non-trivial value, function call, `pytest.approx`, etc.);
  - OR its assertions live inside a `with pytest.raises(...)` block (the
    `with` IS the assertion — the assert body just holds the exercise);
  - OR it has zero `assert` statements at all (e.g. pytest.raises-only tests
    already covered by the second rule).

A test function FAILS if it has ≥1 `assert` and ALL of them are vague and
NONE are inside a `pytest.raises` context.

Scope: every file matching `tests/**/*.py`.

Exit codes:
  0 — clean (or self-test passed)
  1 — violations found
  2 — usage error / self-test failure
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path


def _is_vague_compare(node: ast.Compare) -> bool:
    """Return True if `node` is a vague comparison (per classifier)."""
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    op = node.ops[0]
    rhs = node.comparators[0]
    if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(rhs, ast.Constant) and rhs.value is None:
        return True
    if (
        isinstance(op, (ast.Gt, ast.GtE, ast.NotEq))
        and isinstance(rhs, ast.Constant)
        and rhs.value == 0
    ):
        return True
    if isinstance(op, ast.Eq) and isinstance(rhs, ast.Constant) and isinstance(rhs.value, bool):
        return True
    # Name == Name is the vague case (`assert result == expected` — which
    # side is the answer?). An Attribute on EITHER side is a legitimate
    # anchor (enum member, dataclass field, module constant) and is
    # allowed through — this closes the drift-eval 2026-08-22 wave-3
    # false-positive class where `assert side == TradeSide.BUY` was
    # flagged and authors were forced to wrap RHS in `list(...)` or add
    # a redundant `assert len(x) == N` companion.
    return isinstance(op, ast.Eq) and isinstance(rhs, ast.Name) and isinstance(node.left, ast.Name)


def _is_vague_assertion(assert_node: ast.Assert) -> bool:
    """Return True if the assertion is vague."""
    test = assert_node.test
    # Bare `assert x` (single Name)
    if isinstance(test, ast.Name):
        return True
    if isinstance(test, ast.Compare):
        return _is_vague_compare(test)
    return False


def _asserts_in_raises_block(fn: ast.FunctionDef) -> set[int]:
    """Return line numbers of assertions inside `with pytest.raises(...)` blocks."""
    protected: set[int] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            # match `pytest.raises(...)` and `raises(...)` (star-imported form)
            func = call.func
            is_raises = (isinstance(func, ast.Attribute) and func.attr == "raises") or (
                isinstance(func, ast.Name) and func.id == "raises"
            )
            if not is_raises:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Assert):
                    protected.add(inner.lineno)
    return protected


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (lineno, test_name, snippet) violations for `path`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        asserts = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
        if not asserts:
            continue
        protected = _asserts_in_raises_block(node)
        non_protected = [a for a in asserts if a.lineno not in protected]
        if not non_protected:
            continue
        if all(_is_vague_assertion(a) for a in non_protected):
            first = non_protected[0]
            snippet = ast.unparse(first).strip()
            violations.append((first.lineno, node.name, snippet))
    return violations


def _run_self_test() -> int:
    """Run built-in PASS/FAIL fixtures. Exit 0 if all match, 2 otherwise."""
    pass_cases = [
        # meaningful comparison
        "def test_ok():\n    x = 1\n    assert x == 1\n",
        # pytest.raises whitelist
        (
            "import pytest\ndef test_raises():\n"
            "    with pytest.raises(ValueError):\n        int('x')\n"
        ),
        # meaningful alongside vague — vague is defensive, meaningful is real
        "def test_mix():\n    r = {'a': 1}\n    assert r is not None\n    assert r['a'] == 1\n",
        # Name == Enum.MEMBER — Attribute on RHS is a legit anchor
        (
            "def test_enum_rhs():\n"
            "    side = 'BUY'\n"
            "    class TradeSide:\n        BUY = 'BUY'\n"
            "    assert side == TradeSide.BUY\n"
        ),
        # obj.field == value — Attribute on LHS is also legit
        (
            "def test_attr_lhs():\n"
            "    class O:\n        pass\n"
            "    o = O(); o.field = 3\n"
            "    value = 3\n"
            "    assert o.field == value\n"
        ),
    ]
    fail_cases = [
        # sole is not None
        "def test_bad_none():\n    r = object()\n    assert r is not None\n",
        # sole > 0
        "def test_bad_gt():\n    n = 5\n    assert n > 0\n",
        # bare Name
        "def test_bad_bare():\n    r = True\n    assert r\n",
        # Name == Name — the real vague case, MUST stay rejected
        (
            "def test_bad_name_eq_name():\n"
            "    result = 1\n    expected = 1\n"
            "    assert result == expected\n"
        ),
    ]
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, src in enumerate(pass_cases):
            p = Path(tmp) / f"pass_{i}.py"
            p.write_text(src)
            hits = check_file(p)
            if hits:
                print(f"FAIL: PASS case {i} flagged: {hits}", file=sys.stderr)
                failed = 1
            else:
                print(f"PASS: PASS case {i} clean")
        for i, src in enumerate(fail_cases):
            p = Path(tmp) / f"fail_{i}.py"
            p.write_text(src)
            hits = check_file(p)
            if not hits:
                print(f"FAIL: FAIL case {i} not flagged", file=sys.stderr)
                failed = 1
            else:
                print(f"PASS: FAIL case {i} caught {hits[0][1]}")
    if failed:
        print("\nSELF-TEST FAILED", file=sys.stderr)
        return 2
    print("\nAll self-tests passed.")
    return 0


def main(argv: list[str]) -> int:
    """Entry point. Argv: optional file paths; default = all tests/**/*.py."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _run_self_test()
    if argv:
        paths = []
        for a in argv:
            p = Path(a)
            if p.is_dir():
                paths.extend(sorted(p.rglob("*.py")))
            elif a.endswith(".py"):
                paths.append(p)
    else:
        paths = sorted(Path("tests").rglob("*.py"))
    all_violations: list[tuple[Path, int, str, str]] = []
    for p in paths:
        if not p.exists():
            continue
        for lineno, name, snippet in check_file(p):
            all_violations.append((p, lineno, name, snippet))
    if not all_violations:
        return 0
    print("::error::vague-only test assertions detected", file=sys.stderr)
    for path, lineno, name, snippet in all_violations:
        print(f"{path}:{lineno}: {name} — only vague assertion ({snippet})", file=sys.stderr)
    print(
        "\nSee docs/guides/ai-authored-tests.md §'Failure modes' → Assertion inflation.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
