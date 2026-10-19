#!/usr/bin/env python3
"""Unit-test standard AST guard.

Fails a test function that can't catch a real bug, under six rules of the
unit-test standard in ``docs/guides/ai-authored-tests.md`` (#691, #1193):

- **U3, vague only.** Every assertion is vague: ``is None`` / ``is not None``,
  ``> 0`` / ``>= 0`` / ``!= 0``, ``== True`` / ``== False``, a bare name, a
  name compared with a name, ``isinstance(...)``, ``callable(...)``,
  ``all(...)`` / ``any(...)``, or a length compared with a literal.
- **U3, an ``or``.** Any assertion whose top-level test is ``a or b``: it
  passes when either side holds, so it can't tell which behaviour happened.
  Assert the one value the rule calls for. (Counted under U3; a test with a
  meaningful assertion elsewhere is still flagged.)
- **U6, tests Python or the type checker.** Its only checks are that a
  dataclass is frozen (``FrozenInstanceError``), that an object has slots or
  no ``__dict__``, that a value is an instance of a ``*Port`` protocol, or a
  function's signature.
- **U7, restates a constant.** Every assertion compares an UPPER_CASE
  constant with a literal. Signed parameters are pinned once, in one table
  test; everywhere else the logic that uses them is tested.
- **U10, mock-only.** Its only checks are that a mock was called
  (``assert_called*`` / ``assert_awaited*`` / ``assert_not_called``,
  ``.called``, ``.call_count``, ``.call_args``).

- **U11, no assertion.** The test has no ``assert``, no ``pytest.raises`` /
  ``warns`` / ``fail``, no mock check and no call to a helper whose name
  starts with ``assert``. Running without an exception proves nothing; check
  the state afterwards (standard rule 11).

A test is fine when at least one check is meaningful. Assertions inside a
``with pytest.raises(...)`` block count as the raise being the assertion,
except a ``raises(FrozenInstanceError)``, which is a U6 check.

Baseline: ``scripts/dev/no-vague-test-assertions.baseline`` lists the
violations that existed when the standard was adopted, one
``path::test_name RULE`` per line. Those are reported but don't fail; the
cleanup under #691 removes them. A full run (no paths) also fails on a
baseline line that no longer matches a violation, so the list only shrinks.
``--write-baseline`` rewrites it from a full run; a reviewer sees the diff.

Called by:
  - .pre-commit-config.yaml ``no-vague-test-assertions`` hook (staged tests)
  - .pre-commit-config.yaml ``no-vague-test-assertions-self-test`` hook
  - Manually: ``uv run python scripts/dev/no-vague-test-assertions.py [PATH...]``

Env vars:
  HERMES_SELF_TEST  — set to 1 to run built-in pass/fail fixtures and exit

Exit codes:
  0 — clean (or self-test passed)
  1 — violations outside the baseline, or stale baseline lines
  2 — usage error / self-test failure
"""

import ast
import os
import re
import sys
import tempfile
from pathlib import Path

BASELINE = Path(__file__).with_name("no-vague-test-assertions.baseline")

RULE_TEXT = {
    "U3": "only vague assertions (standard rule 3)",
    "U6": "tests Python or the type checker, not hermes (standard rule 6)",
    "U7": "only restates a constant (standard rule 7)",
    "U10": "only checks that a mock was called (standard rule 10)",
    "U11": "has no assertion (standard rule 11)",
}

_VAGUE_CALLS = frozenset({"isinstance", "callable", "all", "any"})
_MOCK_ASSERTS = re.compile(r"^assert_(called|awaited|not_called|not_awaited|has_calls|any_call)")
_MOCK_ATTRS = frozenset({"called", "call_count", "call_args", "call_args_list", "await_count"})
_UPPER = re.compile(r"^_*[A-Z][A-Z0-9_]*$")


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_vague_compare(node: ast.Compare, name_kinds: dict[str, str] | None = None) -> bool:
    """Return True if ``node`` is a vague comparison.

    ``name_kinds`` classifies the test's local names (see ``_name_kinds``).
    """
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
    if _call_name(node.left) == "len" and isinstance(rhs, ast.Constant):
        return True
    if not (
        isinstance(op, ast.Eq) and isinstance(rhs, ast.Name) and isinstance(node.left, ast.Name)
    ):
        return False
    # Name == Name: a result compared with a hand-built expected value is what
    # standard rule 2 asks for (#756). It is vague when both sides are plain
    # literals (a tautology) or both are computed (two runs of the code).
    known = name_kinds or {}
    kinds = {known.get(rhs.id, "computed"), known.get(node.left.id, "computed")}
    return kinds in ({"literal"}, {"computed"})


def _is_hand_built(value: ast.AST, oracles: frozenset[str]) -> bool:
    """Return True for a literal, a container, a constructor or a test-file helper call.

    ``oracles`` are the functions the test file defines itself: an expected
    value they compute comes from the test, not from the code under test.
    """
    if isinstance(value, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)):
        return True
    name = _call_name(value)
    return name is not None and (name[:1].isupper() or name in oracles)


def _name_kinds(fn: ast.FunctionDef, oracles: frozenset[str] = frozenset()) -> dict[str, str]:
    """Classify each local name as ``literal``, ``built`` (by hand) or ``computed``."""
    kinds: dict[str, str] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Constant):
            kind = "literal"
        elif _is_hand_built(node.value, oracles):
            kind = "built"
        else:
            kind = "computed"
        for target in node.targets:
            if isinstance(target, ast.Name):
                previous = kinds.get(target.id)
                kinds[target.id] = kind if previous in (None, kind) else "computed"
    return kinds


def _is_vague_assertion(assert_node: ast.Assert, name_kinds: dict[str, str] | None = None) -> bool:
    """Return True if the assertion can't tell a right value from a wrong one."""
    test = assert_node.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        test = test.operand
    if isinstance(test, ast.Name):
        return True
    if _call_name(test) in _VAGUE_CALLS:
        return True
    if isinstance(test, ast.Compare):
        return _is_vague_compare(test, name_kinds)
    return False


def _is_python_check(assert_node: ast.Assert) -> bool:
    """Return True if the assertion checks Python or typing rather than hermes (U6)."""
    test = assert_node.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        test = test.operand
    if _call_name(test) == "hasattr" and isinstance(test, ast.Call) and len(test.args) == 2:
        attr = test.args[1]
        if isinstance(attr, ast.Constant) and attr.value in ("__dict__", "__slots__"):
            return True
    if _call_name(test) == "isinstance" and isinstance(test, ast.Call) and len(test.args) == 2:
        kind = test.args[1]
        name = kind.id if isinstance(kind, ast.Name) else getattr(kind, "attr", "")
        if name.endswith("Port"):
            return True
    return any(_call_name(n) == "signature" for n in ast.walk(test))


def _is_constant_restatement(assert_node: ast.Assert) -> bool:
    """Return True if the assertion compares an UPPER_CASE constant with a literal (U7)."""
    test = assert_node.test
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    sides = (test.left, test.comparators[0])

    def is_constant_name(node: ast.AST) -> bool:
        ident = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        return isinstance(ident, str) and bool(_UPPER.match(ident))

    def is_literal(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if _call_name(node) in ("Decimal", "timedelta") and isinstance(node, ast.Call):
            return all(isinstance(a, ast.Constant) for a in node.args)
        return False

    return any(is_constant_name(a) and is_literal(b) for a, b in (sides, tuple(reversed(sides))))


def _is_mock_check(node: ast.AST) -> bool:
    """Return True for ``m.assert_called*()`` calls and ``m.called``-style reads (U10)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return bool(_MOCK_ASSERTS.match(node.func.attr))
    if isinstance(node, ast.Assert):
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            test = test.operand
        if isinstance(test, ast.Compare):
            test = test.left
        # Only a bare mock read counts; a tuple that also pins a real value is meaningful.
        return isinstance(test, ast.Attribute) and test.attr in _MOCK_ATTRS
    return False


def _raises_blocks(fn: ast.FunctionDef) -> list[tuple[ast.With, bool]]:
    """Return each ``with pytest.raises(...)`` block and whether it expects FrozenInstanceError."""
    blocks: list[tuple[ast.With, bool]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if _call_name(call) != "raises" or not isinstance(call, ast.Call):
                continue
            expected = call.args[0] if call.args else None
            frozen = expected is not None and "FrozenInstanceError" in ast.unparse(expected)
            blocks.append((node, frozen))
    return blocks


_CHECKING_CALLS = frozenset({"raises", "warns", "fail", "deprecated_call"})


def _is_checking_call(node: ast.AST) -> bool:
    """Return True for a call that can fail the test (``assert_*`` helpers, ``pytest.fail``)."""
    name = _call_name(node)
    if name is None:
        return False
    return (
        name.lstrip("_").startswith("assert")
        or name.startswith("run_state_machine")
        or name in _CHECKING_CALLS
    )


def _is_or_assertion(assert_node: ast.Assert) -> bool:
    """Return True if the assertion is ``a or b`` (U3)."""
    return isinstance(assert_node.test, ast.BoolOp) and isinstance(assert_node.test.op, ast.Or)


def classify(fn: ast.FunctionDef, oracles: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """Return ``(rule, snippet)`` when ``fn`` breaks a rule, else ``None``."""
    blocks = _raises_blocks(fn)
    in_raises = {
        inner.lineno
        for block, _ in blocks
        for inner in ast.walk(block)
        if isinstance(inner, ast.Assert)
    }
    meaningful_raises = any(not frozen for _, frozen in blocks)
    frozen_raises = any(frozen for _, frozen in blocks)
    asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert) and n.lineno not in in_raises]
    mock_calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and _is_mock_check(n.value)
    ]
    if meaningful_raises:
        return None

    for a in asserts:
        if _is_or_assertion(a):
            return "U3", ast.unparse(a.test).splitlines()[0].strip()

    python_checks = [a for a in asserts if _is_python_check(a)]
    mock_asserts = [a for a in asserts if _is_mock_check(a)]
    restatements = [a for a in asserts if _is_constant_restatement(a)]
    kinds = _name_kinds(fn, oracles)
    vague = [a for a in asserts if _is_vague_assertion(a, kinds)]
    meaningful = [
        a
        for a in asserts
        if a not in python_checks
        and a not in mock_asserts
        and a not in restatements
        and a not in vague
    ]
    if meaningful:
        return None
    if frozen_raises or python_checks:
        python_check: ast.AST = (
            python_checks[0] if python_checks else next(b for b, f in blocks if f)
        )
        return "U6", ast.unparse(python_check).splitlines()[0].strip()
    if mock_calls or mock_asserts:
        mock_check: ast.AST = mock_asserts[0] if mock_asserts else mock_calls[0]
        return "U10", ast.unparse(mock_check).strip()
    if restatements and len(restatements) + len(vague) == len(asserts):
        return "U7", ast.unparse(restatements[0]).strip()
    if vague:
        return "U3", ast.unparse(vague[0]).strip()
    if not asserts and not frozen_raises and not any(_is_checking_call(n) for n in ast.walk(fn)):
        return "U11", fn.name
    return None


def check_file(path: Path) -> list[tuple[int, str, str, str]]:
    """Return ``(lineno, test_name, rule, snippet)`` for each violation in ``path``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    violations: list[tuple[int, str, str, str]] = []
    oracles = frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test_")
    )
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        found = classify(node, oracles)  # type: ignore[arg-type]
        if found is not None:
            violations.append((node.lineno, node.name, found[0], found[1]))
    return violations


def _key(path: Path, name: str, rule: str) -> str:
    return f"{path.as_posix()}::{name} {rule}"


def _load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _run_self_test() -> int:
    """Run built-in PASS/FAIL fixtures. Exit 0 if all match, 2 otherwise."""
    pass_cases = [
        "def test_assert_helper():\n    assert_closed(run())\n",
        "def test_private_assert_helper():\n    _assert_total(run(), 3)\n",
        "def test_explicit_fail():\n    if run():\n        pytest.fail('ran')\n",
        "def test_state_machine():\n    run_state_machine_as_test(Machine)\n",
        "def test_and_not_or():\n    assert a == 1 and b == 2\n",
        "def test_ok():\n    x = 1\n    assert x == 1\n",
        (
            "import pytest\ndef test_raises():\n"
            "    with pytest.raises(ValueError):\n        int('x')\n"
        ),
        "def test_mix():\n    r = {'a': 1}\n    assert r is not None\n    assert r['a'] == 1\n",
        (
            "def test_hand_built_expected():\n    record = make(3)\n"
            "    expected = Record(stop=3)\n    assert record == expected\n"
        ),
        (
            "def test_enum_rhs():\n    class S:\n        BUY = 'BUY'\n"
            "    side = 'BUY'\n    assert side == S.BUY\n"
        ),
        (
            "def test_mock_and_state():\n    m = object()\n"
            "    assert m.called\n    assert m.total == 3\n"
        ),
        (
            "def test_constant_and_logic():\n    LIMIT = 3\n"
            "    assert LIMIT == 3\n    assert f(4) == 'halt'\n"
        ),
    ]
    fail_cases = [
        ("def test_bad_none():\n    r = object()\n    assert r is not None\n", "U3"),
        ("def test_bad_gt():\n    n = 5\n    assert n > 0\n", "U3"),
        ("def test_bad_bare():\n    r = True\n    assert r\n", "U3"),
        ("def test_bad_names():\n    a = 1\n    b = 1\n    assert a == b\n", "U3"),
        ("def test_bad_isinstance():\n    assert isinstance(f(), int)\n", "U3"),
        ("def test_bad_len():\n    assert len(f()) == 3\n", "U3"),
        (
            "import pytest\nfrom dataclasses import FrozenInstanceError\n"
            "def test_bad_frozen():\n"
            "    with pytest.raises(FrozenInstanceError):\n        o.x = 1\n",
            "U6",
        ),
        ("def test_bad_slots():\n    assert not hasattr(o, '__dict__')\n", "U6"),
        ("def test_bad_port():\n    assert isinstance(adapter, MarketDataPort)\n", "U6"),
        ("def test_bad_or():\n    assert f() == 1 or g() == 1\n", "U3"),
        (
            "def test_bad_or_beside_a_good_one():\n"
            "    assert f() == 1\n    assert 'a' in s or 'b' in s\n",
            "U3",
        ),
        ("def test_bad_no_assert():\n    run()\n", "U11"),
        ("def test_bad_unchecked_close():\n    adapter.close()\n    adapter.close()\n", "U11"),
        ("def test_bad_constant():\n    assert TIME_STOP_DAYS == 90\n", "U7"),
        ("def test_bad_mock():\n    m = make()\n    run(m)\n    m.assert_called_once()\n", "U10"),
        ("def test_bad_mock_count():\n    assert m.call_count == 1\n", "U10"),
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
        for i, (src, rule) in enumerate(fail_cases):
            p = Path(tmp) / f"fail_{i}.py"
            p.write_text(src)
            hits = check_file(p)
            if not hits or hits[0][2] != rule:
                print(f"FAIL: FAIL case {i} expected {rule}, got {hits}", file=sys.stderr)
                failed = 1
            else:
                print(f"PASS: FAIL case {i} caught {hits[0][1]} as {rule}")
    if failed:
        print("\nSELF-TEST FAILED", file=sys.stderr)
        return 2
    print("\nAll self-tests passed.")
    return 0


def main(argv: list[str]) -> int:
    """Entry point. Argv: optional file paths; default = all tests/**/*.py."""
    if os.environ.get("HERMES_SELF_TEST") == "1":
        return _run_self_test()
    write_baseline = "--write-baseline" in argv
    targets = [a for a in argv if a != "--write-baseline"]
    full_run = not targets
    paths: list[Path] = []
    for a in targets:
        p = Path(a)
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.py")))
        elif a.endswith(".py"):
            paths.append(p)
    if full_run:
        paths = sorted(Path("tests").rglob("*.py"))

    found: dict[str, tuple[Path, int, str, str, str]] = {}
    for p in paths:
        if not p.exists():
            continue
        for lineno, name, rule, snippet in check_file(p):
            found[_key(p, name, rule)] = (p, lineno, name, rule, snippet)

    if write_baseline:
        if not full_run:
            print("--write-baseline needs a full run (no paths)", file=sys.stderr)
            return 2
        header = "# Known unit-test standard violations (#691). This list only shrinks.\n"
        body = header + "".join(f"{k}\n" for k in sorted(found))
        BASELINE.write_text(body, encoding="utf-8", newline="\n")
        print(f"wrote {len(found)} baseline entries to {BASELINE}")
        return 0

    baseline = _load_baseline()
    new = {k: v for k, v in found.items() if k not in baseline}
    stale = sorted(baseline - set(found)) if full_run else []
    for path, lineno, name, rule, snippet in new.values():
        print(f"{path}:{lineno}: {name} — {RULE_TEXT[rule]}: {snippet}", file=sys.stderr)
    for line in stale:
        print(f"stale baseline entry, remove it: {line}", file=sys.stderr)
    if new or stale:
        print(
            "\nSee docs/guides/ai-authored-tests.md § The unit-test standard.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
