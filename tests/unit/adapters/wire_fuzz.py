"""Shared strategies and checks for the wire-parser fuzz properties (issue #842).

Every parser that reads bytes Hermes did not write gets one property: for
any input, it returns a valid value or raises its own typed error, nothing
else. A raw ``KeyError``, ``TypeError``, ``AttributeError`` or
``decimal.InvalidOperation`` escaping a parser skips the caller's retry,
degraded-feed and alert handling, which only catch the adapter's error.

Inputs start from a well-formed seed shaped like the recorded payloads under
``tests/fixtures`` (values synthetic) and apply up to three structural
mutations: a field replaced by a hostile value (NaN, Infinity, huge or
negative numbers, wrong types, unicode digits), a key or list item removed,
an extra key or item added, a container emptied, or a node wrapped in a list.

The decimal values a parser accepts are checked against the domain: finite,
times timezone-aware, OHLC ordered, bar times aligned to the interval.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import os
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import previous_boundary
from hermes.domain.market_data.kline import Kline

__all__ = [
    "FUZZ",
    "FUZZ_SLOW",
    "REFUSED",
    "accepted",
    "bar_violations",
    "clean_violations",
    "foreign_error",
    "kline_violations",
    "mutated",
    "mutated_text",
    "numeric_strings",
    "price_violations",
]

_EXAMPLES: Final = int(os.environ.get("HERMES_FUZZ_EXAMPLES", "40"))

FUZZ: Final = settings(
    max_examples=_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
"""Examples per parser: modest, so the unit tier grows by seconds, not minutes.

A search for new counterexamples raises it: ``HERMES_FUZZ_EXAMPLES=2000
HYPOTHESIS_PROFILE=explore uv run pytest -k wire_fuzz``.
"""

FUZZ_SLOW: Final = settings(FUZZ, max_examples=max(1, _EXAMPLES // 2))
"""For properties whose single example touches the filesystem or SQLite."""

numeric_strings: Final = st.sampled_from(
    [
        "0",
        "-0",
        "-1",
        "-0.00000001",
        "0.00000001",
        "1",
        "100",
        "1e3",
        "1E+999999",
        "1E-999999",
        "9" * 40,
        "NaN",
        "-Infinity",
        "Infinity",
        "sNaN",
        "".join(map(chr, (0x661, 0x660, 0x660))),  # Arabic-Indic digits: Decimal reads 100
        "".join(map(chr, (0xFF11, 0xFF10, 0xFF10))),  # full-width digits: Decimal reads 100
        " 100 ",
        "1_000",
        "0x10",
        "",
    ]
)
"""Strings a numeric wire field may hold: the type is right, the value is not."""

_HOSTILE_CONSTANTS: Final[tuple[object, ...]] = (
    None,
    True,
    False,
    0,
    -1,
    2**63,
    -(2**63),
    10**30,
    0.1,
    -0.0,
    1e308,
    math.nan,
    math.inf,
    -math.inf,
    "",
    "\x00",
    "é€\U0001f642",
    "2026-13-45T25:61:00",
    "true",
    [],
    {},
    [[]],
    {"": None},
)

hostile_values: Final = st.one_of(
    st.sampled_from(_HOSTILE_CONSTANTS),
    numeric_strings,
    st.integers(min_value=-(2**64), max_value=2**64),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=6),
)
"""Any single JSON-representable value a field could arrive as."""

_OPS: Final = ("replace", "replace", "replace", "delete", "add", "empty", "wrap")

type _Path = tuple[str | int, ...]


def _paths(node: object, prefix: _Path = ()) -> Iterator[_Path]:
    yield prefix
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _paths(child, (*prefix, index))


def _child(node: object, step: str | int) -> object:
    if isinstance(node, dict):
        return node[step]
    if isinstance(node, list) and isinstance(step, int):
        return node[step]
    raise LookupError(step)


def _get(root: object, path: _Path) -> object:
    node = root
    for step in path:
        node = _child(node, step)
    return node


def _set(root: object, path: _Path, value: object) -> object:
    if not path:
        return value
    parent, step = _get(root, path[:-1]), path[-1]
    if isinstance(parent, dict) or (isinstance(parent, list) and isinstance(step, int)):
        parent[step] = value
    return root


def _apply(root: object, path: _Path, op: str, value: object, key: str) -> object:
    node = _get(root, path)
    if op == "delete" and path:
        parent, step = _get(root, path[:-1]), path[-1]
        if isinstance(parent, dict) or (isinstance(parent, list) and isinstance(step, int)):
            del parent[step]
        return root
    if op == "add" and isinstance(node, dict):
        node[key] = value
        return root
    if op == "add" and isinstance(node, list):
        node.append(value)
        return root
    if op == "empty" and isinstance(node, dict | list):
        return _set(root, path, type(node)())
    if op == "wrap":
        return _set(root, path, [node])
    return _set(root, path, value)


@st.composite
def mutated(
    draw: st.DrawFn, seed: object, *, values: st.SearchStrategy[object] | None = None
) -> object:
    """Return a deep copy of ``seed`` with zero to three structural mutations.

    Args:
        draw: Hypothesis draw function.
        seed: A well-formed payload (JSON-shaped: dicts, lists, scalars).
        values: What a replaced or added field may hold; defaults to
            :data:`hostile_values`.

    Returns:
        The mutated payload. Zero mutations returns the seed unchanged, so
        the accepting path is exercised as often as the refusing ones.

    """
    pool = hostile_values if values is None else values
    root = copy.deepcopy(seed)
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        paths = list(_paths(root))
        path = paths[draw(st.integers(min_value=0, max_value=len(paths) - 1))]
        op = draw(st.sampled_from(_OPS))
        value = copy.deepcopy(draw(pool))  # constants like [] must never be shared
        root = _apply(root, path, op, value, draw(st.text(max_size=4)))
    return root


@st.composite
def mutated_text(draw: st.DrawFn, seed: object) -> str | bytes:
    """Return ``seed`` mutated and serialised, sometimes truncated or not JSON at all.

    ``json.dumps`` writes NaN and Infinity as bare tokens, which Python's
    ``json.loads`` reads back: the same thing a hostile peer can send.
    """
    text = json.dumps(draw(mutated(seed)))
    return draw(
        st.one_of(
            st.just(text),
            st.integers(min_value=0, max_value=len(text)).map(lambda cut: text[:cut]),
            st.just(text.encode("utf-8")),
            st.binary(max_size=12),
            st.text(max_size=12),
        )
    )


class _Refused:
    """Marks an input the parser refused with its own error."""

    def __repr__(self) -> str:
        return "REFUSED"


REFUSED: Final = _Refused()


def foreign_error(
    parse: Callable[[], object], error: type[Exception] | tuple[type[Exception], ...]
) -> str:
    """Run ``parse`` and name any exception other than ``error``; ``""`` when none escaped."""
    try:
        parse()
    except error:
        return ""
    except Exception as exc:  # the point is to name whatever escaped
        return f"{type(exc).__name__}: {exc!r}"[:300]
    return ""


def accepted(
    parse: Callable[[], object], error: type[Exception] | tuple[type[Exception], ...]
) -> object:
    """Return what ``parse`` returns, or :data:`REFUSED` when it raises ``error``."""
    try:
        return parse()
    except error:
        return REFUSED


def _walk(value: object) -> Iterator[object]:
    yield value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk(key)
            yield from _walk(item)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _walk(item)


def clean_violations(value: object, *, utc: bool = True) -> list[str]:
    """Return every non-finite number or naive (or, with ``utc``, non-UTC) time in ``value``.

    Walks dataclasses, mappings and sequences. :data:`REFUSED` has nothing
    inside it, so a refused input reports no violation.
    """
    zero = UTC.utcoffset(None)
    found: list[str] = []
    for item in _walk(value):
        if isinstance(item, Decimal) and not item.is_finite():
            found.append(f"non-finite Decimal {item!r}")
        elif isinstance(item, float) and not math.isfinite(item):
            found.append(f"non-finite float {item!r}")
        elif isinstance(item, datetime) and item.utcoffset() is None:
            found.append(f"naive time {item!r}")
        elif isinstance(item, datetime) and utc and item.utcoffset() != zero:
            found.append(f"time not UTC {item!r}")
    return found


def price_violations(value: object) -> list[str]:
    """Return a problem when an accepted price is not a finite :class:`Decimal` above zero."""
    if value is REFUSED:
        return []
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        return [f"not a finite positive Decimal: {value!r}"]
    return []


def bar_violations(value: object) -> list[str]:
    """Return every domain invariant a parsed :class:`Bar` breaks.

    Finite and positive prices, non-negative volume, ``high`` above every
    other price and ``low`` below, and an ``open_time`` on the interval grid.
    """
    if not isinstance(value, Bar):
        return clean_violations(value)
    prices = (value.open, value.high, value.low, value.close)
    return clean_violations(value) + _ohlc_violations(
        prices, value.volume, value.open_time, value.close_time, value
    )


def kline_violations(value: object) -> list[str]:
    """Return every domain invariant a parsed :class:`Kline` breaks (as :func:`bar_violations`)."""
    if not isinstance(value, Kline):
        return clean_violations(value)
    prices = (value.open.amount, value.high.amount, value.low.amount, value.close.amount)
    return clean_violations(value) + _ohlc_violations(
        prices, value.volume.amount, value.open_time, value.close_time, value
    )


def _ohlc_violations(
    prices: tuple[Decimal, Decimal, Decimal, Decimal],
    volume: Decimal,
    open_time: datetime,
    close_time: datetime,
    bar: Bar | Kline,
) -> list[str]:
    open_, high, low, close = prices
    checks = {
        "a price is not positive": all(p > 0 for p in prices),
        "volume is negative": volume >= 0,
        "high is below another price": high >= max(open_, low, close),
        "low is above another price": low <= min(open_, high, close),
        "close_time is not after open_time": close_time > open_time,
        "open_time is off the interval grid": previous_boundary(open_time, bar.interval)
        == open_time,
    }
    return [f"{name}: {bar!r}" for name, holds in checks.items() if not holds]
