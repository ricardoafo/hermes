"""Source scan: the Madrid zone, the cent, the UTC check and the exact context each have one home.

Issue #1180 found the Madrid zone built 11 times, ``Decimal("0.01")`` 9
times, three UTC checks that disagreed and eight exact-Decimal contexts at two
widths. This scan fails the moment a copy comes back, wherever it is written
and however it is spelled (``Decimal("1E-2")`` is still a cent).

Tests may build their own zone or cent: the scan covers ``src/hermes`` only.
"""

import ast
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

import pytest

from tests.support.paths import REPO_ROOT

_SRC = REPO_ROOT / "src" / "hermes"
_TIME = "domain/value_objects/time.py"
_CENTS = "domain/value_objects/cents.py"
_PRECISION = "domain/value_objects/precision.py"

type _Count = Callable[[ast.Module], int]

_CENT_ALLOWED = {
    # The stop-limit buffer is 1% of the stop price, a fraction, not a cent.
    "adapters/exchanges/binance_spot.py": 1,
    "adapters/exchanges/kraken_spot.py": 1,
}


def _trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(_SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(_SRC.rglob("*.py"))
    ]


_TREES = _trees()


def _called(node: ast.AST, name: str) -> ast.Call | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return node if called == name else None


def _string_arg(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _is_cent(text: str) -> bool:
    try:
        return Decimal(text) == Decimal("0.01")
    except InvalidOperation:
        return False


def madrid_zones(tree: ast.Module) -> int:
    """Count ``ZoneInfo("Europe/Madrid")`` calls in ``tree``."""
    return sum(
        1
        for node in ast.walk(tree)
        if (call := _called(node, "ZoneInfo")) is not None and _string_arg(call) == "Europe/Madrid"
    )


def cents(tree: ast.Module) -> int:
    """Count ``Decimal`` calls whose text is a cent, in any spelling."""
    return sum(
        1
        for node in ast.walk(tree)
        if (call := _called(node, "Decimal")) is not None
        and (text := _string_arg(call)) is not None
        and _is_cent(text)
    )


def utc_checks(tree: ast.Module) -> int:
    """Count function definitions named like a UTC check."""
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.lstrip("_") == "require_utc"
    )


def precision_settings(tree: ast.Module) -> int:
    """Count ``x.prec = ...`` assignments and ``Context(prec=...)`` calls."""
    assigned = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "prec" for t in node.targets)
    )
    built = sum(
        1
        for node in ast.walk(tree)
        if (call := _called(node, "Context")) is not None
        and any(keyword.arg == "prec" for keyword in call.keywords)
    )
    return assigned + built


def _found(count: _Count, home: str, allowed: dict[str, int] | None = None) -> dict[str, int]:
    allowed = allowed or {}
    return {
        rel: n
        for rel, tree in _TREES
        if rel != home and (n := count(tree)) and n != allowed.get(rel)
    }


def test_the_madrid_zone_is_built_only_in_its_home() -> None:
    """Every module reads ``MADRID`` from ``domain/value_objects/time.py``."""
    assert _found(madrid_zones, _TIME) == {}


def test_the_cent_is_built_only_in_its_home() -> None:
    """Every module reads ``CENT`` or ``to_cents`` from ``domain/value_objects/cents.py``."""
    assert _found(cents, _CENTS, _CENT_ALLOWED) == {}


def test_the_utc_check_is_defined_only_in_its_home() -> None:
    """One ``require_utc``; the three copies disagreed on Madrid-aware times."""
    assert _found(utc_checks, _TIME) == {}


def test_decimal_precision_is_set_only_in_its_home() -> None:
    """One exact context; there were eight, at 60 and 100 digits."""
    assert _found(precision_settings, _PRECISION) == {}


@pytest.mark.parametrize(
    ("source", "count", "expected"),
    [
        ('MADRID = ZoneInfo("Europe/Madrid")', madrid_zones, 1),
        ('zone = zoneinfo.ZoneInfo("Europe/Madrid")', madrid_zones, 1),
        ('zone = ZoneInfo("Europe/Lisbon")', madrid_zones, 0),
        ('CENT = Decimal("0.01")', cents, 1),
        ('CENT = decimal.Decimal("1E-2")', cents, 1),
        ('CENT = Decimal(".010")', cents, 1),
        ('TENTH = Decimal("0.1")', cents, 0),
        ('BAD = Decimal("not a number")', cents, 0),
        ("def _require_utc(value): ...", utc_checks, 1),
        ("def require_utc(value): ...", utc_checks, 1),
        ("def require_aware(value): ...", utc_checks, 0),
        ("ctx.prec = 60", precision_settings, 1),
        ("CTX = Context(prec=60)", precision_settings, 1),
        ("CTX = decimal.Context(rounding=ROUND_HALF_UP)", precision_settings, 0),
    ],
)
def test_the_scan_sees_each_spelling(source: str, count: _Count, expected: int) -> None:
    """A copy written another way is still found; a different value is not."""
    assert count(ast.parse(source)) == expected


def test_the_allowed_buffers_are_still_there() -> None:
    """The allow-list only names files that still hold the 1% buffer, so it cannot grow stale."""
    counts = {rel: cents(tree) for rel, tree in _TREES if rel in _CENT_ALLOWED}
    assert counts == {
        "adapters/exchanges/binance_spot.py": 1,
        "adapters/exchanges/kraken_spot.py": 1,
    }
