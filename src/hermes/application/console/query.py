"""One query, filter and sort language for every console list.

A list exposes its rows as mappings of field name to value and names the
fields a query may use. The operator types one line:

- ``field:value`` keeps rows whose field equals the value (case-insensitive);
- ``field>=value``, ``<=``, ``>`` and ``<`` compare: as numbers when the row
  holds a number, as ISO text for dates, as case-insensitive text otherwise;
- ``since:7d`` or ``since:24h`` keeps rows whose ``at`` timestamp is that recent;
- ``level`` compares by severity, not by spelling: ``debug < info < warn <
  error < critical`` (``warn`` and ``warning`` are the same level), so
  ``level>=warn`` keeps warnings and worse, and a level column sorts by severity;
- anything else is free text, searched in every value; quotes keep spaces
  (``"stop raised"``, ``coin:"two words"``).

All terms must hold. The shared facets every list draws from are
:data:`SHARED_FACETS`. Parsing is strict: an unknown field, a missing value or
an open quote raises :class:`QueryError` with a message the bar can show as is.

Sorting is stable over several :class:`SortKey` values; empty values sort last
whichever way a key points. Everything here is pure, so a list filters the same
way in tests, in the TUI and anywhere else.

Reference: openspec/changes/729-query-bar/change.md;
openspec/changes/716-logs-screen/change.md (the level order and the
``service`` / ``trace`` fields).
"""

from __future__ import annotations

import difflib
import os
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import partial
from typing import Final

__all__ = [
    "AT_FIELD",
    "LEVEL_FIELD",
    "LEVEL_ORDER",
    "SHARED_FACETS",
    "SINCE_FIELD",
    "Condition",
    "FacetCount",
    "FreeText",
    "Op",
    "Query",
    "QueryError",
    "Row",
    "Since",
    "SortKey",
    "Term",
    "complete",
    "completions",
    "display_value",
    "facet_counts",
    "filter_rows",
    "format_query",
    "format_sort",
    "level_rank",
    "matches",
    "parse_query",
    "sort_rows",
    "toggle_sort",
]

SHARED_FACETS: Final = (
    "year",
    "month",
    "day",
    "coin",
    "kind",
    "state",
    "level",
    "service",
    "trace",
    "run",
)
SINCE_FIELD: Final = "since"
AT_FIELD: Final = "at"
LEVEL_FIELD: Final = "level"
LEVEL_ORDER: Final = ("debug", "info", "warning", "error", "critical")

type Row = Mapping[str, object]

_MAX_SORT_KEYS: Final = 3
_CLOSE_MATCH: Final = 0.75
_TERM = re.compile(r"([A-Za-z_]+)(>=|<=|>|<|:)(.*)", re.DOTALL)
_SINCE_VALUE = re.compile(r"([0-9]+)([hdHD])")
_NEEDS_QUOTES = re.compile(r"[\s:<>=]")
_SINCE_HELP: Final = "since takes hours or days, like since:24h or since:7d"
_LEVEL_HELP: Final = "level takes debug, info, warn, error or critical, like level>=warn"
_LEVEL_ALIASES: Final = {"warn": "warning"}
_QUOTE_HELP: Final = 'quotes must wrap a whole value, like "two words"'


class QueryError(ValueError):
    """A query the parser cannot read; ``str()`` is the message to show."""


class Op(StrEnum):
    """How a condition compares the row's value with the written one."""

    EQ = ":"
    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"


@dataclass(frozen=True, slots=True)
class Condition:
    """``field`` ``op`` ``value``, as written (the value is not converted yet)."""

    field: str
    op: Op
    value: str


@dataclass(frozen=True, slots=True)
class FreeText:
    """A word or quoted phrase searched in every value of a row."""

    text: str


@dataclass(frozen=True, slots=True)
class Since:
    """``since:<amount><unit>``: rows no older than that; unit ``h`` or ``d``."""

    amount: int
    unit: str

    @property
    def delta(self) -> timedelta:
        """Return the window as a duration."""
        return timedelta(hours=self.amount) if self.unit == "h" else timedelta(days=self.amount)


type Term = Condition | FreeText | Since


@dataclass(frozen=True, slots=True)
class Query:
    """A parsed query: its terms in the order they were written, all required."""

    terms: tuple[Term, ...] = ()

    @property
    def conditions(self) -> tuple[Condition, ...]:
        """Return the field conditions, in order."""
        return tuple(t for t in self.terms if isinstance(t, Condition))

    @property
    def text(self) -> tuple[str, ...]:
        """Return the free-text terms, in order."""
        return tuple(t.text for t in self.terms if isinstance(t, FreeText))

    @property
    def since(self) -> tuple[timedelta, ...]:
        """Return every ``since`` window, in order."""
        return tuple(t.delta for t in self.terms if isinstance(t, Since))


@dataclass(frozen=True, slots=True)
class SortKey:
    """One sort level; earlier keys in a sequence take priority."""

    field: str
    descending: bool = False


@dataclass(frozen=True, slots=True)
class FacetCount:
    """How many rows hold one value of a facet."""

    value: str
    count: int


# ---------------------------------------------------------------------------
# Parsing and formatting
# ---------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        elif char.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if quoted:
        raise QueryError('a quote is not closed; add the closing "')
    if current:
        tokens.append("".join(current))
    return tokens


def _unquote(raw: str) -> str:
    if raw.startswith('"'):
        inner = raw[1:-1]
        if len(raw) < 2 or not raw.endswith('"') or '"' in inner:  # noqa: PLR2004
            raise QueryError(_QUOTE_HELP)
        return inner
    if '"' in raw:
        raise QueryError(_QUOTE_HELP)
    return raw


def _unknown_field(name: str, known: Sequence[str]) -> QueryError:
    close = difflib.get_close_matches(name, known, n=1, cutoff=_CLOSE_MATCH)
    hint = f' (did you mean "{close[0]}"?)' if close else ""
    return QueryError(f'unknown field "{name}"{hint}; fields: {", ".join(known)}')


def _term(token: str, fields: Collection[str]) -> Term | None:
    if token.startswith('"'):
        text = _unquote(token)
        return FreeText(text) if text else None
    found = _TERM.fullmatch(token)
    if found is None:
        return FreeText(_unquote(token))
    name, op, raw = found.group(1).lower(), Op(found.group(2)), found.group(3)
    if name == SINCE_FIELD:
        since = _SINCE_VALUE.fullmatch(raw)
        if op is not Op.EQ or since is None or int(since.group(1)) == 0:
            raise QueryError(_SINCE_HELP)
        return Since(int(since.group(1)), since.group(2).lower())
    if name not in fields:
        raise _unknown_field(name, _known(fields))
    value = _unquote(raw)
    if not value:
        raise QueryError(f'"{name}{op.value}" needs a value, like {name}:{_example(name)}')
    if name == LEVEL_FIELD and level_rank(value) is None:
        raise QueryError(_LEVEL_HELP)
    return Condition(name, op, value)


def level_rank(value: object) -> int | None:
    """Return a level's place in :data:`LEVEL_ORDER`; ``None`` for anything else.

    Args:
        value: A level name, any case; ``warn`` counts as ``warning``.

    Returns:
        ``0`` for debug up to ``4`` for critical.
    """
    if not isinstance(value, str):
        return None
    name = value.strip().casefold()
    name = _LEVEL_ALIASES.get(name, name)
    return LEVEL_ORDER.index(name) if name in LEVEL_ORDER else None


def _example(field: str) -> str:
    return {
        "coin": "BTC",
        "year": "2026",
        "month": "2026-10",
        "day": "2026-10-05",
        "level": "warn",
        "service": "engine",
    }.get(field, "value")


def _known(fields: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted({*fields, SINCE_FIELD}))


def parse_query(text: str, fields: Collection[str] = SHARED_FACETS) -> Query:
    """Parse one line of query text.

    Args:
        text: What the operator typed.
        fields: Field names this list accepts in conditions; ``since`` is
            always accepted.

    Returns:
        The query, terms in written order; an empty line gives an empty query.

    Raises:
        QueryError: An unknown field, a missing value, a bad ``since`` or a
            misplaced quote; the message says what to write instead.
    """
    terms = (_term(token, fields) for token in _tokens(text))
    return Query(tuple(t for t in terms if t is not None))


def _quote_if_needed(text: str) -> str:
    return f'"{text}"' if _NEEDS_QUOTES.search(text) else text


def _format_term(term: Term) -> str:
    if isinstance(term, Since):
        return f"{SINCE_FIELD}:{term.amount}{term.unit}"
    if isinstance(term, FreeText):
        return _quote_if_needed(term.text)
    return f"{term.field}{term.op.value}{_quote_if_needed(term.value)}"


def format_query(query: Query) -> str:
    """Write a query back as text that :func:`parse_query` reads to the same query.

    Args:
        query: The query to write.

    Returns:
        The canonical text, terms separated by one space.
    """
    return " ".join(_format_term(term) for term in query.terms)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def display_value(value: object) -> str:
    """Return a row value the way cells, facets and text search see it.

    Args:
        value: A row value.

    Returns:
        ``""`` for ``None``, ISO text for dates, plain decimal text for numbers.
    """
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    return Decimal(str(value)) if isinstance(value, float) else Decimal(value)


def _compare(field: str, value: object, written: str) -> int | None:
    if field == LEVEL_FIELD:
        have_rank, want_rank = level_rank(value), level_rank(written)
        if have_rank is None or want_rank is None:
            return None
        return (have_rank > want_rank) - (have_rank < want_rank)
    number = _number(value)
    if number is not None:
        try:
            wanted = Decimal(written)
        except InvalidOperation:
            return None
        if not wanted.is_finite():
            return None
        return (number > wanted) - (number < wanted)
    have, want = display_value(value).casefold(), written.casefold()
    return (have > want) - (have < want)


def _passes(op: Op, compared: int) -> bool:
    match op:
        case Op.EQ:
            return compared == 0
        case Op.GE:
            return compared >= 0
        case Op.LE:
            return compared <= 0
        case Op.GT:
            return compared > 0
        case Op.LT:
            return compared < 0


def _holds(term: Term, row: Row, now: datetime) -> bool:
    if isinstance(term, Since):
        at = row.get(AT_FIELD)
        return isinstance(at, datetime) and at >= now - term.delta
    if isinstance(term, FreeText):
        needle = term.text.casefold()
        return any(needle in display_value(v).casefold() for v in row.values())
    value = row.get(term.field)
    if value is None:
        return False
    compared = _compare(term.field, value, term.value)
    return compared is not None and _passes(term.op, compared)


def matches(query: Query, row: Row, *, now: datetime) -> bool:
    """Return whether one row passes every term of ``query``.

    Args:
        query: The parsed query.
        row: Field name to value.
        now: The moment ``since`` windows end at.

    Returns:
        ``True`` when every term holds; a missing or empty field fails its condition.
    """
    return all(_holds(term, row, now) for term in query.terms)


def filter_rows[R: Row](rows: Iterable[R], query: Query, *, now: datetime) -> tuple[R, ...]:
    """Return the rows ``query`` keeps, in their incoming order.

    Args:
        rows: The list's rows.
        query: The parsed query.
        now: The moment ``since`` windows end at.

    Returns:
        The matching rows.
    """
    return tuple(row for row in rows if matches(query, row, now=now))


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def _sort_value(value: object) -> tuple[int, Decimal | str]:
    number = _number(value)
    if number is not None:
        return (0, number)
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo is not None else value
        return (1, moment.isoformat())
    if isinstance(value, date):
        return (2, value.isoformat())
    return (3, display_value(value).casefold())


def _field_sort_value(field: str, row: Row) -> tuple[int, Decimal | str]:
    value = row[field]
    rank = level_rank(value) if field == LEVEL_FIELD else None
    return _sort_value(value) if rank is None else (0, Decimal(rank))


def sort_rows[R: Row](rows: Iterable[R], keys: Sequence[SortKey]) -> tuple[R, ...]:
    """Sort rows by several keys, stably; empty values go last on every key.

    Args:
        rows: The rows to order.
        keys: Sort levels, highest priority first.

    Returns:
        The rows in order; rows equal on every key keep their incoming order.
    """
    ordered = list(rows)
    for key in reversed(keys):
        present = [r for r in ordered if r.get(key.field) is not None]
        empty = [r for r in ordered if r.get(key.field) is None]
        present.sort(key=partial(_field_sort_value, key.field), reverse=key.descending)
        ordered = present + empty
    return tuple(ordered)


def toggle_sort(keys: Sequence[SortKey], field: str) -> tuple[SortKey, ...]:
    """Apply one header click (or ``S``) on ``field`` to the current sort.

    Args:
        keys: The current sort levels, highest priority first.
        field: The column clicked.

    Returns:
        The primary key flipped when ``field`` already leads; otherwise
        ``field`` ascending in front of the previous keys, at most three.
    """
    if keys and keys[0].field == field:
        return (SortKey(field, not keys[0].descending), *keys[1:])
    rest = tuple(k for k in keys if k.field != field)
    return (SortKey(field), *rest)[:_MAX_SORT_KEYS]


def format_sort(keys: Sequence[SortKey]) -> str:
    """Return the sort indicator, e.g. ``coin ▲ · at ▼``; empty when unsorted."""
    return " · ".join(f"{k.field} {'▼' if k.descending else '▲'}" for k in keys)


# ---------------------------------------------------------------------------
# Facets and completion
# ---------------------------------------------------------------------------


def facet_counts(rows: Iterable[Row], field: str) -> tuple[FacetCount, ...]:
    """Count the rows holding each value of ``field``.

    Args:
        rows: The rows to count (usually the filtered ones).
        field: The facet.

    Returns:
        One count per value, most common first, ties by value; rows without
        a value are not counted.
    """
    firsts: dict[str, object] = {}
    counter: Counter[str] = Counter()
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        shown = display_value(value)
        firsts.setdefault(shown, value)
        counter[shown] += 1
    ordered = sorted(counter, key=lambda v: (-counter[v], _sort_value(firsts[v])))
    return tuple(FacetCount(v, counter[v]) for v in ordered)


def completions(prefix: str, fields: Collection[str]) -> tuple[str, ...]:
    """Return the field names (``since`` included) that start with ``prefix``.

    Args:
        prefix: What has been typed of the field name; case does not matter.
        fields: The list's field names.

    Returns:
        Matching names, sorted.
    """
    wanted = prefix.casefold()
    return tuple(name for name in _known(fields) if name.startswith(wanted))


def complete(text: str, fields: Collection[str]) -> str:
    """Complete the field name being typed at the end of ``text`` (Tab).

    Args:
        text: The query line so far.
        fields: The list's field names.

    Returns:
        ``text`` with its last word completed to ``field:`` when one field
        fits, to the shared prefix when several do, unchanged otherwise (no
        word, a word that already has an operator or a quote, or no match).
    """
    word = text.rpartition(" ")[2]
    if not word or '"' in word or any(op.value in word for op in Op):
        return text
    names = completions(word, fields)
    if not names:
        return text
    filled = f"{names[0]}:" if len(names) == 1 else os.path.commonprefix(list(names))
    if len(filled) <= len(word):
        return text
    return text[: len(text) - len(word)] + filled
