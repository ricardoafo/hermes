"""Live prices between bars: cadence, tick board and live maths (issue #713).

Invariants:

- the ticker is asked for BTC, ETH and SOL once every 10 s of the console
  clock, never more often (A-1.1);
- a failing fetch never raises: it becomes a failed tick, and the coin keeps
  its last good price, marked with when it failed (A-1.2);
- more than 60 s without a good tick pauses the live layer (A-1.3);
- the ring buffer keeps the newest 90 ticks, and the change is measured over
  them (D-5);
- a live position at the booked price gives back the booked figures, and live
  figures use the live price for every price term (D-4);
- a live total exists only when every open position has a live price, and it
  is then cash plus every position at its live price: never half live, half
  booked (D-4, property).

Reference: openspec/changes/713-live-ticker/change.md.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.live import (
    RING_TICKS,
    LiveBoard,
    LiveState,
    TickerPoller,
    TickOutcome,
    live_position,
    live_total,
    spread_bps,
)
from hermes.application.console.position_figures import LotFacts, PositionFacts, build_detail
from hermes.application.console.views import (
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    TaxPending,
    WaterfallColumn,
)
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair

_T0 = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_RATE = Decimal("0.8")
_TOLERANCE = Decimal("1e-18")


class _FakeTicker:
    """Answers every pair with a fixed last price, or raises for the coins in ``fail``."""

    def __init__(self, prices: dict[str, str], fail: dict[str, Exception] | None = None) -> None:
        self.prices = {coin: Decimal(price) for coin, price in prices.items()}
        self.fail = fail or {}
        self.calls: list[str] = []

    def get_ticker(self, pair: Pair) -> Ticker:
        coin = pair.base.value
        self.calls.append(f"{coin}/{pair.quote.value}")
        if coin in self.fail:
            raise self.fail[coin]
        price = self.prices[coin]
        return Ticker(
            pair=pair,
            best_bid=price - 1,
            best_ask=price + 1,
            last_price=price,
            snapshot_time=_T0,
        )


class _Clock:
    def __init__(self, at: datetime = _T0) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _ok(coin: str, price: str, at: datetime) -> TickOutcome:
    return TickOutcome(coin=coin, at=at, price=Decimal(price))


def _failed(coin: str, at: datetime) -> TickOutcome:
    return TickOutcome(coin=coin, at=at, price=None, error="KrakenPublicError")


def _spread(coin: str, price: str, bps: str | None, at: datetime) -> TickOutcome:
    return TickOutcome(
        coin=coin, at=at, price=Decimal(price), spread_bps=None if bps is None else Decimal(bps)
    )


# ----------------------------------------------------------------------
# Cadence and fetch
# ----------------------------------------------------------------------


def test_poller_asks_each_coin_once_every_ten_seconds() -> None:
    """Over 60 s stepped every second, the ticker is called at 0, 10, ... 50 s: 6 x 3 coins."""
    clock = _Clock()
    fake = _FakeTicker({"BTC": "60000", "ETH": "2600", "SOL": "140"})
    poller = TickerPoller(fake, clock)
    polled_at = []
    for second in range(60):
        clock.at = _T0 + timedelta(seconds=second)
        if poller.due(clock.at):
            poller.begin(clock.at)
            poller.fetch()
            polled_at.append(second)
    assert (polled_at, len(fake.calls), fake.calls[:3]) == (
        [0, 10, 20, 30, 40, 50],
        18,
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    )


def test_poller_is_not_due_again_before_ten_seconds() -> None:
    """A poll started at t is not due at t + 9.999 s and is due at t + 10 s."""
    poller = TickerPoller(_FakeTicker({}), _Clock())
    poller.begin(_T0)
    assert (
        poller.due(_T0 + timedelta(seconds=9, milliseconds=999)),
        poller.due(_T0 + timedelta(seconds=10)),
    ) == (False, True)


def test_fetch_turns_every_failure_into_a_failed_tick() -> None:
    """A Kraken error and any other exception become failed ticks; nothing is raised."""
    fake = _FakeTicker(
        {"BTC": "60000"},
        fail={"ETH": KrakenPublicError("Kraken said no"), "SOL": RuntimeError("socket closed")},
    )
    outcomes = TickerPoller(fake, _Clock()).fetch()
    assert [(o.coin, o.price, o.error, o.at) for o in outcomes] == [
        ("BTC", Decimal(60000), None, _T0),
        ("ETH", None, "KrakenPublicError", _T0),
        ("SOL", None, "RuntimeError", _T0),
    ]


# ----------------------------------------------------------------------
# Board
# ----------------------------------------------------------------------


def test_failed_tick_keeps_the_last_price_and_says_when_it_failed() -> None:
    """After a good tick at t0 and a failed one at t0+10s, the price is t0's, failed at t0+10s."""
    board = LiveBoard()
    later = _T0 + timedelta(seconds=10)
    board.apply([_ok("BTC", "60000", _T0)])
    board.apply([_failed("BTC", later)])
    quote = board.quote("BTC")
    assert quote is not None
    assert (quote.price, quote.at, quote.failed_at) == (Decimal(60000), _T0, later)


def test_next_good_tick_clears_the_failure() -> None:
    """A good tick after a failure is a plain live price again."""
    board = LiveBoard()
    board.apply([_ok("BTC", "60000", _T0)])
    board.apply([_failed("BTC", _T0 + timedelta(seconds=10))])
    board.apply([_ok("BTC", "60100", _T0 + timedelta(seconds=20))])
    quote = board.quote("BTC")
    assert quote is not None
    assert (quote.price, quote.failed_at) == (Decimal(60100), None)


def test_a_coin_that_never_ticked_has_no_quote() -> None:
    """A failure with no earlier price gives no quote at all, never a zero price."""
    board = LiveBoard()
    board.apply([_failed("SOL", _T0)])
    assert (board.quote("SOL"), board.prices()) == (None, {})


def test_status_waits_then_goes_live_then_pauses_after_sixty_seconds() -> None:
    """Waiting before any tick; live up to 60 s after the last good tick; paused after."""
    board = LiveBoard()
    waiting = board.status(_T0)
    board.apply([_ok("ETH", "2600", _T0)])
    live = board.status(_T0 + timedelta(seconds=60))
    paused = board.status(_T0 + timedelta(seconds=61))
    assert (waiting.state, live.state, paused.state, paused.last_tick_at) == (
        LiveState.WAITING,
        LiveState.LIVE,
        LiveState.PAUSED,
        _T0,
    )


def test_failed_ticks_do_not_keep_the_layer_live() -> None:
    """Failures for 61 s after the last good tick still pause the live layer."""
    board = LiveBoard()
    board.apply([_ok("ETH", "2600", _T0)])
    for second in (10, 20, 30, 40, 50, 60):
        board.apply([_failed("ETH", _T0 + timedelta(seconds=second))])
    assert board.status(_T0 + timedelta(seconds=61)).state is LiveState.PAUSED


def test_ring_keeps_the_newest_ticks_and_measures_the_change_over_them() -> None:
    """95 ticks of 100, 101, ... keep 105 to 194; the change is 194 / 105 - 1."""
    board = LiveBoard()
    for index in range(RING_TICKS + 5):
        board.apply([_ok("SOL", str(100 + index), _T0 + timedelta(seconds=10 * index))])
    quote = board.quote("SOL")
    assert quote is not None
    assert (len(quote.history), quote.history[0], quote.history[-1], quote.change_pct) == (
        RING_TICKS,
        Decimal(105),
        Decimal(194),
        (Decimal(194) / Decimal(105) - 1) * 100,
    )


def test_apply_names_the_coins_whose_price_moved_and_which_way() -> None:
    """Up is 1, down is -1; a first price and an unchanged price do not flash."""
    board = LiveBoard()
    first = board.apply([_ok("BTC", "60000", _T0), _ok("ETH", "2600", _T0)])
    later = _T0 + timedelta(seconds=10)
    moved = board.apply(
        [_ok("BTC", "60010", later), _ok("ETH", "2590", later), _ok("SOL", "140", later)]
    )
    same = board.apply([_ok("BTC", "60010", later + timedelta(seconds=10))])
    assert (first, moved, same) == ({}, {"BTC": 1, "ETH": -1}, {})


# ----------------------------------------------------------------------
# Live maths
# ----------------------------------------------------------------------


def _detail(
    *, current: str = "52000", rate: Decimal = _RATE, path: tuple[str, ...] = ("50000", "53000")
) -> PositionDetail:
    lot = LotFacts(
        remaining_qty=Decimal("0.5"),
        original_qty=Decimal("0.5"),
        opened_at=_T0,
        price_eur=Decimal(40000),
        fee_eur=Decimal(20),
    )
    return build_detail(
        PositionFacts(
            strategy="swing-donchian-4h",
            coin="BTC",
            timeframe="4h",
            size=Decimal("0.5"),
            cost_native=Decimal(25000),
            current_price=Decimal(current),
            stop_price=Decimal(48000),
            atr=Decimal(1000),
            signal_at=_T0,
            lots=(lot,),
            price_path=tuple(Decimal(p) for p in (*path, current)),
            eur_per_usdt=rate,
        )
    )


def test_live_position_at_the_booked_price_gives_back_the_booked_figures() -> None:
    """At the booked mark, gross, net, change and stop distance are the booked figures."""
    detail = _detail()
    row = detail.row
    live = live_position(row, detail, row.current_price)
    assert live.gross is not None and live.net is not None and live.stop_distance_atr is not None
    assert abs(live.gross - detail.gross) <= _TOLERANCE
    assert abs(live.net - row.net) <= _TOLERANCE
    assert abs(live.stop_distance_atr - row.stop_distance_atr) <= _TOLERANCE
    assert (live.change_pct, live.stop_distance_pct, live.price_eur) == (
        row.change_pct,
        row.stop_distance_pct,
        row.current_eur,
    )


def test_live_position_values_the_worked_example_at_the_live_price() -> None:
    """0.5 BTC at 54000 x 0.8: gross 1600, to close 21.6, net 1558.4, +8%, stop 4 ATR."""
    detail = _detail()
    live = live_position(detail.row, detail, Decimal(54000))
    assert (
        live.price_eur,
        live.gross,
        live.net,
        live.change_pct,
        live.stop_distance_pct,
        live.stop_distance_atr,
        live.high,
    ) == (
        Decimal(43200),
        Decimal(1600),
        Decimal("1558.4"),
        Decimal(8),
        Decimal(6000) * 100 / Decimal(54000),
        Decimal(6),
        Decimal(54000),
    )


def test_live_stop_room_is_measured_from_the_live_price() -> None:
    """Live 50500 with stop 48000 and high 53000: (50500 - 48000) / 5000 = 0.5 of the room."""
    detail = _detail()
    assert live_position(detail.row, detail, Decimal(50500)).stop_room == Decimal("0.5")


def test_live_position_without_detail_has_gross_but_no_net() -> None:
    """With no detail the fees are unknown, so net is not guessed; gross is size x (now - entry)."""
    row = _detail().row
    live = live_position(row, None, Decimal(54000))
    assert (live.gross, live.net) == (Decimal("0.5") * (Decimal(43200) - Decimal(40000)), None)


def test_eur_pending_position_has_no_eur_figure() -> None:
    """A row not priced in EUR yet gets a USDT change and no EUR price, gross or net."""
    row = replace(_detail().row, eur_pending=True, entry_price_eur=None)
    live = live_position(row, None, Decimal(55000))
    assert (live.price_eur, live.gross, live.net, live.change_pct) == (
        None,
        None,
        None,
        (Decimal(55000) / Decimal(50000) - 1) * 100,
    )


def _summary(*, total: str, change: str, cash: str, eur_pending: bool = False) -> PortfolioSummary:
    column = WaterfallColumn(gross=Decimal(0), fees=Decimal(0), tax=TaxPending())
    return PortfolioSummary(
        range=PortfolioRange.TODAY,
        as_of=_T0,
        total_value=Decimal(total),
        change=Decimal(change),
        change_pct=Decimal(0),
        net=Decimal(0),
        sparkline=(),
        cash=Decimal(cash),
        invested=Decimal(total) - Decimal(cash),
        today=column,
        year=column,
        open_pnl=Decimal(0),
        open_tax_if_sold=TaxPending(),
        tax_band=None,
        eur_pending=eur_pending,
    )


def test_live_total_revalues_every_position_and_moves_the_change() -> None:
    """Cash 1000 + 0.5 BTC moving 52000 -> 54000 at 0.8: total +800, change 100 -> 900."""
    row = _detail().row
    summary = _summary(total="21800", change="100", cash="1000")
    total = live_total(summary, [row], {"BTC": Decimal(54000)})
    assert total is not None
    assert (total.total, total.change, total.change_pct) == (
        Decimal(22600),
        Decimal(900),
        Decimal(900) * 100 / Decimal(21700),
    )


@pytest.mark.parametrize(
    ("prices", "pending"),
    [({}, False), ({"ETH": Decimal(2600)}, False), ({"BTC": Decimal(54000)}, True)],
    ids=["no-price", "other-coin-only", "eur-pending"],
)
def test_live_total_is_absent_unless_every_position_is_live(
    prices: dict[str, Decimal], pending: bool
) -> None:
    """No live total when a position has no live price or EUR is pending."""
    row = _detail().row
    summary = _summary(total="21800", change="100", cash="1000", eur_pending=pending)
    assert (live_total(summary, [row], prices), summary.total_value) == (None, Decimal(21800))


def test_live_total_is_absent_with_no_open_position() -> None:
    """Cash alone does not move with the market, so there is nothing live to show."""
    summary = _summary(total="1000", change="0", cash="1000")
    assert (live_total(summary, [], {}), summary.total_value) == (None, Decimal(1000))


# ----------------------------------------------------------------------
# Properties: live and booked never mix in one figure
# ----------------------------------------------------------------------

_PRICE = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(100000), places=2)
_SIZE = st.decimals(min_value=Decimal("0.001"), max_value=Decimal(10), places=3)
_EUR_RATE = st.decimals(min_value=Decimal("0.5"), max_value=Decimal(2), places=4)
_CASH = st.decimals(min_value=Decimal(0), max_value=Decimal(100000), places=2)


@st.composite
def _book(draw: st.DrawFn) -> tuple[PortfolioSummary, list[PositionRow], dict[str, Decimal]]:
    """Return a booked summary over 1-3 open positions and a live price per coin."""
    cash = draw(_CASH)
    rows: list[PositionRow] = []
    prices = {}
    booked = cash
    for coin in draw(st.lists(st.sampled_from(["BTC", "ETH", "SOL"]), min_size=1, unique=True)):
        size, current, rate = draw(_SIZE), draw(_PRICE), draw(_EUR_RATE)
        row = replace(_detail().row, coin=coin, size=size, current_price=current, eur_per_usdt=rate)
        rows.append(row)
        prices[coin] = draw(_PRICE)
        booked += size * current * rate
    summary = _summary(total=str(booked), change="0", cash=str(cash))
    return summary, rows, prices


@given(book=_book())
def test_property_live_total_is_cash_plus_every_position_at_its_live_price(
    book: tuple[PortfolioSummary, list[PositionRow], dict[str, Decimal]],
) -> None:
    """The live total holds no booked price: it equals cash + sum(size x live x rate)."""
    summary, rows, prices = book
    total = live_total(summary, rows, prices)
    expected = summary.cash + sum(
        (row.size * prices[row.coin] * row.eur_per_usdt for row in rows),
        Decimal(0),
    )
    assert total is not None
    assert abs(total.total - expected) <= _TOLERANCE


@given(book=_book(), missing=st.sampled_from(["BTC", "ETH", "SOL"]))
def test_property_one_missing_live_price_means_no_live_total(
    book: tuple[PortfolioSummary, list[PositionRow], dict[str, Decimal]], missing: str
) -> None:
    """Drop one open position's live price and the live total disappears; it never half-mixes."""
    summary, rows, prices = book
    held = {row.coin for row in rows}
    prices.pop(missing, None)
    expected_none = missing in held
    assert (live_total(summary, rows, prices) is None) is expected_none


@given(live=_PRICE)
def test_property_live_figures_use_only_the_live_price(live: Decimal) -> None:
    """The same position booked at two different marks gives the same live figures."""
    low, high = _detail(current="51000"), _detail(current="53500")
    a = live_position(low.row, low, live)
    b = live_position(high.row, high, live)
    assert a.change_pct == b.change_pct
    assert a.stop_distance_pct == b.stop_distance_pct
    assert a.gross is not None and b.gross is not None
    assert abs(a.gross - b.gross) <= _TOLERANCE
    assert a.net is not None and b.net is not None
    assert abs(a.net - b.net) <= _TOLERANCE


# ----------------------------------------------------------------------
# Invested at live prices (issue #1162)
# ----------------------------------------------------------------------


@given(book=_book())
def test_property_live_cash_plus_live_invested_is_the_live_total(
    book: tuple[PortfolioSummary, list[PositionRow], dict[str, Decimal]],
) -> None:
    """Cash does not move between bars, so the live invested figure is the live total less cash."""
    summary, rows, prices = book
    total = live_total(summary, rows, prices)
    assert total is not None
    assert abs(summary.cash + total.invested - total.total) <= _TOLERANCE


def test_live_invested_moves_by_the_open_positions_price_change_only() -> None:
    """One position of size 2 x 1 EUR/USDT whose price rises by 10 adds 20 EUR to the invested."""
    detail = _detail()
    row = replace(detail.row, size=Decimal(2), current_price=Decimal(100), eur_per_usdt=Decimal(1))
    summary = _summary(total="1000", change="0", cash="800")
    total = live_total(summary, [row], {row.coin: Decimal(110)})
    assert total is not None
    assert (total.invested, total.total) == (Decimal(220), Decimal(1020))


# ----------------------------------------------------------------------
# Spread (issue #1156)
# ----------------------------------------------------------------------


def test_spread_is_the_gap_over_the_mid_in_basis_points() -> None:
    """A bid of 99 and an ask of 101 around a mid of 100 is 200 bp."""
    assert spread_bps(Decimal(99), Decimal(101)) == Decimal(200)


def test_fetch_carries_the_spread_of_each_coin_and_none_for_a_failure() -> None:
    """A book of price -1 / +1 gives 2 / 60000 x 10000 bp for BTC; a failed coin has none."""
    fake = _FakeTicker({"BTC": "60000"}, fail={"ETH": RuntimeError("socket closed")})
    outcomes = TickerPoller(fake, _Clock(), coins=("BTC", "ETH")).fetch()
    assert [o.spread_bps for o in outcomes] == [Decimal(2) / Decimal(60000) * 10000, None]


def test_the_quote_carries_the_newest_good_spread() -> None:
    """The second good tick's spread replaces the first's."""
    board = LiveBoard()
    board.apply([_spread("BTC", "60000", "4.0", _T0)])
    board.apply([_spread("BTC", "60000", "6.5", _T0 + timedelta(seconds=10))])
    quote = board.quote("BTC")
    assert quote is not None
    assert quote.spread_bps == Decimal("6.5")


def test_a_good_tick_without_a_spread_clears_the_old_one() -> None:
    """A spread is never carried over a tick that did not measure one."""
    board = LiveBoard()
    board.apply([_spread("BTC", "60000", "4.0", _T0)])
    board.apply([_spread("BTC", "60000", None, _T0 + timedelta(seconds=10))])
    quote = board.quote("BTC")
    assert [q.spread_bps for q in [quote] if q is not None] == [None]


@given(
    bid=st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10**6), places=2),
    gap=st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10**4), places=2),
    scale=st.integers(min_value=1, max_value=1000),
)
def test_property_spread_is_positive_and_does_not_depend_on_the_price_unit(
    bid: Decimal, gap: Decimal, scale: int
) -> None:
    """An uncrossed book has a positive spread, and quoting in another unit leaves it as it was."""
    ask = bid + gap
    here = spread_bps(bid, ask)
    there = spread_bps(bid * scale, ask * scale)
    assert (here > 0, abs(here - there) <= _TOLERANCE * 10**6) == (True, True)
