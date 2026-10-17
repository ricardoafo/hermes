"""How one klines reply's rows become bars: order, exact repeats and conflicting repeats (#1013).

The parser is exercised directly; the polling loop that calls it is covered
by ``test_binance_live_klines.py`` against the fake Binance public API.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.market_data import binance_live_klines
from hermes.domain.errors import ExchangeError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.adapters.market_data.fake_binance_public import row_for

_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4: Final = KlineInterval.H4
_START: Final = datetime(2024, 3, 1, tzinfo=UTC)
_OPENS: Final = tuple(_START + timedelta(hours=4 * i) for i in range(6))


def _parse(rows: list[object]) -> list[Bar]:
    return binance_live_klines._parse_rows(rows, _PAIR, _H4, keep=lambda _: True)


def _row(open_time: datetime) -> list[object]:
    return row_for("BTCUSDT", _H4, open_time)


@given(order=st.permutations(_OPENS))
def test_rows_in_any_order_come_out_ascending(order: list[datetime]) -> None:
    """Invariant: the bars are in ascending ``open_time`` whatever order the rows came in."""
    bars = _parse([_row(moment) for moment in order])

    assert [bar.open_time for bar in bars] == list(_OPENS)


@given(repeats=st.lists(st.sampled_from(_OPENS), max_size=12))
def test_an_exact_repeat_of_a_row_is_dropped(repeats: list[datetime]) -> None:
    """Invariant: repeating identical rows yields each bar once, the same as without repeats."""
    rows = [_row(moment) for moment in _OPENS] + [_row(moment) for moment in repeats]

    assert _parse(rows) == _parse([_row(moment) for moment in _OPENS])


def test_two_different_rows_for_one_bar_are_refused() -> None:
    """Invariant: a reply that gives one bar two different volumes raises, never picks one."""
    conflicting = _row(_OPENS[1])
    conflicting[5] = "99.00000000"
    rows = [_row(moment) for moment in _OPENS] + [conflicting]

    with pytest.raises(ExchangeError, match="two different rows for 2024-03-01T04:00:00"):
        _parse(rows)
