"""The vendor contract for every payload the fake market serves.

One checker per endpoint. Each returns the list of ways a payload breaks the
contract, empty when it conforms. Three sources must pass the same checkers:

- the recorded production responses in ``tests/fixtures/``;
- the live responses the daily contract run fetches (``tests/contract/``);
- every payload :mod:`tests.support.fake_market.wire` generates.

So a format change at the vendor fails the contract run, and a generator that
drifts from the vendor fails the fake-market tests (issue #688).
"""

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval

KLINE_FIELDS = 12
_DECIMAL_TEXT = re.compile(r"^\d+\.\d+$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_ONE_MS = timedelta(milliseconds=1)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _decimal(value: object) -> Decimal | None:
    """Return the decimal a Binance number string holds, or ``None`` if it is not one."""
    if not isinstance(value, str) or not _DECIMAL_TEXT.match(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def kline_row_problems(row: object, interval: KlineInterval | None = None) -> list[str]:
    """Return how one ``/api/v3/klines`` row breaks the contract.

    Args:
        row: One decoded row.
        interval: When given, ``close_time`` must be ``open_time + interval - 1 ms``.

    Returns:
        The problems found; empty when the row conforms.

    """
    if not isinstance(row, list):
        return [f"row is {type(row).__name__}, not a list"]
    if len(row) != KLINE_FIELDS:
        return [f"row has {len(row)} fields, not {KLINE_FIELDS}"]
    problems = [f"field {i} is not an integer" for i in (0, 6, 8) if not _is_int(row[i])]
    prices = {name: _decimal(row[i]) for i, name in enumerate(("o", "h", "l", "c"), start=1)}
    problems += [
        f"price {n} is not an 8-place decimal string" for n, v in prices.items() if v is None
    ]
    problems += [
        f"field {i} is not a decimal string" for i in (5, 7, 9, 10) if _decimal(row[i]) is None
    ]
    if not isinstance(row[11], str):
        problems.append("field 11 is not a string")
    if problems:
        return problems
    o, h, lo, c = (prices[n] for n in ("o", "h", "l", "c"))
    assert o is not None and h is not None and lo is not None and c is not None
    if h < max(o, c) or lo > min(o, c):
        problems.append("high/low do not bracket open and close")
    open_ms, close_ms = row[0], row[6]
    assert isinstance(open_ms, int) and isinstance(close_ms, int)
    if close_ms <= open_ms:
        problems.append("close_time is not after open_time")
    if interval is not None:
        expected = open_ms + interval_duration(interval) // _ONE_MS - 1
        if close_ms != expected:
            problems.append(f"close_time is not open_time + {interval.value} - 1 ms")
    return problems


def klines_problems(payload: object, interval: KlineInterval | None = None) -> list[str]:
    """Return how a ``/api/v3/klines`` body breaks the contract (rows ascending, no repeats)."""
    if not isinstance(payload, list):
        return [f"body is {type(payload).__name__}, not a list"]
    problems = [
        f"row {i}: {p}" for i, row in enumerate(payload) for p in kline_row_problems(row, interval)
    ]
    if problems:
        return problems
    opens = [row[0] for row in payload if isinstance(row, list)]
    if opens != sorted(set(opens)):
        problems.append("rows are not in strictly ascending open_time order")
    return problems


def time_problems(payload: object) -> list[str]:
    """Return how a ``/api/v3/time`` body breaks the contract."""
    if not isinstance(payload, dict) or set(payload) != {"serverTime"}:
        return [f"body is not {{'serverTime': ms}}: {payload!r}"[:200]]
    return [] if _is_int(payload["serverTime"]) else ["serverTime is not an integer"]


def depth_problems(payload: object, levels: int | None = None) -> list[str]:
    """Return how a ``/api/v3/depth`` body breaks the contract.

    Args:
        payload: Decoded body.
        levels: When given, each side must hold exactly this many levels.

    Returns:
        The problems found; empty when the book conforms.

    """
    if not isinstance(payload, dict) or set(payload) != {"lastUpdateId", "bids", "asks"}:
        return ["body is not {lastUpdateId, bids, asks}"]
    problems = [] if _is_int(payload["lastUpdateId"]) else ["lastUpdateId is not an integer"]
    sides: dict[str, list[Decimal]] = {}
    for side in ("bids", "asks"):
        rows = payload[side]
        if not isinstance(rows, list):
            problems.append(f"{side} is not a list")
            continue
        if levels is not None and len(rows) != levels:
            problems.append(f"{side} has {len(rows)} levels, not {levels}")
        prices: list[Decimal] = []
        for i, level in enumerate(rows):
            pair = [_decimal(x) for x in level] if isinstance(level, list) else []
            if len(pair) != 2 or None in pair:
                problems.append(f"{side}[{i}] is not [price, quantity] decimal strings")
                continue
            price = pair[0]
            assert price is not None
            prices.append(price)
        sides[side] = prices
    if problems:
        return problems
    bids, asks = sides["bids"], sides["asks"]
    if bids != sorted(bids, reverse=True):
        problems.append("bids are not best (highest) first")
    if asks != sorted(asks):
        problems.append("asks are not best (lowest) first")
    if bids and asks and bids[0] >= asks[0]:
        problems.append("book is crossed: best bid >= best ask")
    return problems


def fx_problems(payload: object) -> list[str]:
    """Return how a Frankfurter ``/latest`` or ``/<date>`` body breaks the contract."""
    if not isinstance(payload, dict) or set(payload) != {"amount", "base", "date", "rates"}:
        return ["body is not {amount, base, date, rates}"]
    problems = []
    if not isinstance(payload["amount"], float):
        problems.append("amount is not a number")
    if not isinstance(payload["base"], str) or not _CURRENCY.match(payload["base"]):
        problems.append("base is not a currency code")
    if not isinstance(payload["date"], str) or not _ISO_DATE.match(payload["date"]):
        problems.append("date is not YYYY-MM-DD")
    rates = payload["rates"]
    if not isinstance(rates, dict) or not rates:
        problems.append("rates is not a non-empty object")
    else:
        problems += [
            f"rate {code!r} is not a positive number"
            for code, value in rates.items()
            if not _CURRENCY.match(str(code))
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
        ]
    return problems
