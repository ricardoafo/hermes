"""Property-based tests for the swing indicator engines (issue #692).

Covers DonchianEngine, AtrEngine, AdxEngine and EmaRegimeFilter. Prices are
drawn as integer cents and converted to Decimal, so no float ever reaches the
price maths. Reference: pre-registration-v1.md §5.1, §5.2, §5.4.
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.strategy.swing.engines.adx import AdxEngine
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.strategy.swing.engines.donchian import DonchianEngine
from hermes.domain.strategy.swing.engines.ema import EmaRegimeFilter
from tests.unit.domain.strategy.swing.conftest import make_bar

_PROPS = settings(max_examples=60)
_TOLERANCE = Decimal("1e-18")

_CENTS = Decimal("0.01")
_MIN_CENTS = 1_000
_MAX_CENTS = 100_000
_MAX_WICK_CENTS = 900


def _price(cents: int) -> Decimal:
    return Decimal(cents) * _CENTS


@st.composite
def _ohlc(draw: st.DrawFn, min_wick: int = 0) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    open_c = draw(st.integers(_MIN_CENTS, _MAX_CENTS))
    close_c = draw(st.integers(_MIN_CENTS, _MAX_CENTS))
    high_c = max(open_c, close_c) + draw(st.integers(min_wick, _MAX_WICK_CENTS))
    low_c = min(open_c, close_c) - draw(st.integers(min_wick, _MAX_WICK_CENTS))
    return _price(open_c), _price(high_c), _price(low_c), _price(close_c)


def _bars_from(
    ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]],
    *,
    shift: Decimal = Decimal(0),
    scale: Decimal = Decimal(1),
) -> list[Bar]:
    return [
        make_bar(
            open_=o * scale + shift,
            high=h * scale + shift,
            low=low * scale + shift,
            close=c * scale + shift,
            index=i,
        )
        for i, (o, h, low, c) in enumerate(ohlcs)
    ]


def _series(min_size: int, max_size: int) -> st.SearchStrategy[list[Bar]]:
    return st.lists(_ohlc(), min_size=min_size, max_size=max_size).map(_bars_from)


def _ohlc_series(
    min_size: int, max_size: int, *, min_wick: int = 0
) -> st.SearchStrategy[list[tuple[Decimal, Decimal, Decimal, Decimal]]]:
    return st.lists(_ohlc(min_wick=min_wick), min_size=min_size, max_size=max_size)


_PERIOD = st.integers(2, 6)
_SHIFT = st.integers(0, 50_000).map(_price)
_SCALE = st.integers(1, 1_000).map(Decimal)


def _close_to(actual: Decimal, expected: Decimal) -> bool:
    return abs(actual - expected) <= _TOLERANCE * max(abs(expected), Decimal(1))


# ── Donchian ────────────────────────────────────────────────────────────────


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(1, 40))
def test_donchian_break_iff_close_exceeds_max_prior_high(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]]
) -> None:
    """high_break is True exactly when the last close > max high of the prior `period` bars."""
    bars = _bars_from(ohlcs)
    if len(bars) < period + 1:
        expected = False
    else:
        expected = bars[-1].close > max(b.high for b in bars[-(period + 1) : -1])
    assert DonchianEngine.high_break(bars, period=period) is expected


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(1, 40), shift=_SHIFT)
def test_donchian_break_invariant_under_constant_price_shift(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]], shift: Decimal
) -> None:
    """Adding a constant to every price never changes the Donchian breakout verdict."""
    base = DonchianEngine.high_break(_bars_from(ohlcs), period=period)
    shifted = DonchianEngine.high_break(_bars_from(ohlcs, shift=shift), period=period)
    assert shifted is base


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(7, 40), extra=st.integers(0, 100_000).map(_price))
def test_donchian_ignores_signal_bar_high(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]], extra: Decimal
) -> None:
    """The signal bar's own high never enters the channel (no-lookahead, SA-4 P1)."""
    bars = _bars_from(ohlcs)
    last = bars[-1]
    raised = [
        *bars[:-1],
        make_bar(
            open_=last.open,
            high=last.high + extra,
            low=last.low,
            close=last.close,
            index=len(bars) - 1,
        ),
    ]
    assert DonchianEngine.high_break(raised, period=period) is DonchianEngine.high_break(
        bars, period=period
    )


@_PROPS
@given(period=_PERIOD, data=st.data())
def test_donchian_false_below_period_plus_one_bars(period: int, data: st.DataObject) -> None:
    """With fewer than period + 1 bars no channel exists, so no breakout is reported."""
    bars = data.draw(_series(0, period))
    assert DonchianEngine.high_break(bars, period=period) is False


# ── ATR ─────────────────────────────────────────────────────────────────────


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(2, 40))
def test_atr_never_negative(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]]
) -> None:
    """ATR is an average of true ranges, so it is never negative."""
    atr = AtrEngine.value(_bars_from(ohlcs), period=period)
    if atr is not None:
        assert atr >= Decimal(0)


@_PROPS
@given(period=_PERIOD, size=st.integers(2, 40), level=st.integers(_MIN_CENTS, _MAX_CENTS))
def test_atr_of_constant_bars_is_zero(period: int, size: int, level: int) -> None:
    """Bars with open == high == low == close at one price have zero true range, so ATR is 0."""
    p = _price(level)
    bars = _bars_from([(p, p, p, p)] * size)
    atr = AtrEngine.value(bars, period=period)
    if size < period + 1:
        assert atr is None
    else:
        assert atr == Decimal(0)


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(2, 40), scale=_SCALE)
def test_atr_scales_linearly_with_prices(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]], scale: Decimal
) -> None:
    """Multiplying every price by k > 0 multiplies ATR by k (within Decimal rounding)."""
    base = AtrEngine.value(_bars_from(ohlcs), period=period)
    scaled = AtrEngine.value(_bars_from(ohlcs, scale=scale), period=period)
    if base is None:
        assert scaled is None
    else:
        assert scaled is not None
        assert _close_to(scaled, base * scale)


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(7, 40), shift=_SHIFT)
def test_atr_invariant_under_constant_price_shift(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]], shift: Decimal
) -> None:
    """True range uses price differences only, so a constant shift leaves ATR unchanged."""
    base = AtrEngine.value(_bars_from(ohlcs), period=period)
    assert base is not None
    assert AtrEngine.value(_bars_from(ohlcs, shift=shift), period=period) == base


# ── ADX ─────────────────────────────────────────────────────────────────────


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(2, 50))
def test_adx_within_zero_and_one_hundred(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]]
) -> None:
    """ADX is a Wilder average of DX values in [0, 100], so it stays in [0, 100]."""
    adx = AdxEngine.value(_bars_from(ohlcs), period=period)
    if adx is not None:
        assert Decimal(0) <= adx <= Decimal(100)


@_PROPS
@given(period=_PERIOD, ohlcs=_ohlc_series(13, 50, min_wick=1), shift=_SHIFT)
def test_adx_invariant_under_constant_price_shift(
    period: int, ohlcs: list[tuple[Decimal, Decimal, Decimal, Decimal]], shift: Decimal
) -> None:
    """DM and TR use price differences only, so a constant shift leaves ADX unchanged."""
    base = AdxEngine.value(_bars_from(ohlcs), period=period)
    assert base is not None
    assert AdxEngine.value(_bars_from(ohlcs, shift=shift), period=period) == base


@_PROPS
@given(period=_PERIOD, data=st.data())
def test_adx_first_value_needs_two_periods_plus_one_bars(period: int, data: st.DataObject) -> None:
    """ADX is None below 2 * period + 1 bars and a value from there on (bars with a range)."""
    ohlcs = data.draw(_ohlc_series(2 * period + 3, 2 * period + 3, min_wick=1))
    bars = _bars_from(ohlcs)
    has_value = [AdxEngine.value(bars[:n], period=period) is not None for n in range(len(bars) + 1)]
    assert has_value == [n >= 2 * period + 1 for n in range(len(bars) + 1)]


# ── EMA ─────────────────────────────────────────────────────────────────────


@_PROPS
@given(period=st.integers(2, 10), data=st.data())
def test_ema_lies_between_min_and_max_close(period: int, data: st.DataObject) -> None:
    """An EMA is a convex combination of closes, so it lies within [min close, max close]."""
    bars = data.draw(_series(period, 3 * period + 5))
    ema = EmaRegimeFilter.ema_value(bars, period=period)
    assert ema is not None
    closes = [b.close for b in bars]
    tol = _TOLERANCE * max(closes)
    assert min(closes) - tol <= ema <= max(closes) + tol


@_PROPS
@given(
    period=st.integers(2, 10),
    extra=st.integers(0, 20),
    level=st.integers(_MIN_CENTS, _MAX_CENTS),
)
def test_ema_of_constant_series_is_that_constant(period: int, extra: int, level: int) -> None:
    """EMA of a series whose every close equals c is c (within Decimal rounding)."""
    p = _price(level)
    bars = _bars_from([(p, p, p, p)] * (period + extra))
    ema = EmaRegimeFilter.ema_value(bars, period=period)
    assert ema is not None
    assert _close_to(ema, p)


@_PROPS
@given(period=st.integers(2, 10), data=st.data())
def test_ema_first_value_needs_period_bars(period: int, data: st.DataObject) -> None:
    """EMA(period) is None below `period` bars and a value from there on."""
    bars = data.draw(_series(period + 2, period + 2))
    has_value = [
        EmaRegimeFilter.ema_value(bars[:n], period=period) is not None for n in range(len(bars) + 1)
    ]
    assert has_value == [n >= period for n in range(len(bars) + 1)]
