"""Tests for the Tax screen's view built from the tax estimate (issue #719).

Reference: openspec/changes/719-tax-screen/change.md (R-1, R-2, R-5).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.views import TaxBand, TaxEstimate, TaxPending
from hermes.application.tax.estimate import TaxYearEstimate, estimate_year
from hermes.application.tax.screen import (
    TaxScreen,
    TaxScreenService,
    bracket_fills,
    build_lots,
    month_subtotals,
    year_fees,
)
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.scale import SavingsScale
from hermes.domain.tax.two_month import TwoMonthFlag
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow
from tests.unit.domain.tax.scales import SCALE_2026

_D = Decimal
_NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _row(
    ref: str,
    asset: Currency,
    side: OrderSide,
    days_ago: int,
    *,
    qty: str = "1",
    price: str = "100",
    fee: str = "1",
    quote_leg: bool = False,
) -> EurTradeRow:
    return EurTradeRow(
        ref=ref,
        asset=asset,
        side=side,
        qty=_D(qty),
        executed_at=_NOW - timedelta(days=days_ago),
        price_eur=_D(price),
        fee_eur=_D(fee),
        quote_leg=quote_leg,
    )


class _Source:
    """A ledger read port over fixed rows."""

    def __init__(self, rows: tuple[EurTradeRow, ...]) -> None:
        self.rows = rows

    def list_eur_trades(self) -> tuple[EurTradeRow, ...]:
        return self.rows


def _estimate(**changes: object) -> TaxYearEstimate:
    base: dict[str, object] = {
        "year": 2026,
        "realised_gain": _D("1000.00"),
        "hermes_tax": TaxEstimate(amount=_D("123.45")),
        "today_tax": TaxEstimate(amount=_D(0)),
        "tax_if_sold_now": TaxEstimate(amount=_D("9.99")),
        "per_position_if_sold_now": (),
        "carry_forward_used": _D(0),
        "carry_forward_left": (),
        "band": TaxBand(rate=_D(21), next_rate=_D(23), room_to_next=_D(43500)),
        "flags": (),
        "notices": (),
        "is_shadow": True,
        "savings_base": _D(6500),
    }
    base.update(changes)
    return TaxYearEstimate(**base)  # type: ignore[arg-type]


class _FakeService:
    """A tax service that answers with a fixed estimate and records the call."""

    def __init__(self, answer: TaxYearEstimate | TaxPending) -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    def estimate(self, **kwargs: object) -> TaxYearEstimate | TaxPending:
        self.calls.append(kwargs)
        return self.answer


def test_bracket_fills_split_the_base_across_the_bands() -> None:
    """Invariant: a base of 7,000 fills the 19% band (6,000) and 1,000 of the 21% band."""
    fills = bracket_fills(SCALE_2026, _D(7000))
    assert [(f.rate, f.filled) for f in fills[:3]] == [
        (_D(19), _D(6000)),
        (_D(21), _D(1000)),
        (_D(23), _D(0)),
    ]
    assert fills[-1].upper is None


@given(base=st.decimals(min_value=-1000, max_value=500000, places=2))
def test_bracket_fills_add_up_to_the_base(base: Decimal) -> None:
    """Invariant: the band fills always add up to the base (zero when it is negative)."""
    fills = bracket_fills(SCALE_2026, base)
    assert sum((f.filled for f in fills), start=_D(0)) == max(base, _D(0))


def test_year_fees_count_base_rows_of_the_year_only() -> None:
    """Invariant: fees sum base rows of the year; quote legs and other years are left out."""
    rows = (
        _row("1", Currency.BTC, OrderSide.BUY, 10, fee="1.10"),
        _row("1q", Currency.USDT, OrderSide.SELL, 10, fee="1.10", quote_leg=True),
        _row("2", Currency.BTC, OrderSide.SELL, 5, fee="0.40"),
        _row("0", Currency.ETH, OrderSide.BUY, 400, fee="7.00"),
    )
    assert year_fees(rows, 2026) == _D("1.50")


def test_build_lots_attributes_each_sale_to_the_lots_it_used() -> None:
    """Invariant: a sale spanning two lots appears under both, with its FIFO quantities."""
    rows = (
        _row("b1", Currency.SOL, OrderSide.BUY, 30, qty="1", price="100", fee="0"),
        _row("b2", Currency.SOL, OrderSide.BUY, 20, qty="1", price="120", fee="0"),
        _row("s1", Currency.SOL, OrderSide.SELL, 10, qty="1.5", price="110", fee="0"),
    )
    lots = {lot.ref: lot for lot in build_lots(rows, flagged=("s1",))}
    assert lots["b1"].state == "closed"
    assert lots["b1"].sales[0].qty == _D(1)
    assert lots["b1"].gain == _D(10)
    assert lots["b2"].state == "open"
    assert lots["b2"].remaining == _D("0.5")
    assert lots["b2"].sales[0].qty == _D("0.5")
    assert lots["b2"].gain == _D(-5)
    assert lots["b1"].two_month is True


def test_month_subtotals_total_cost_and_gain_per_buy_month() -> None:
    """Invariant: lots bought in one month are totalled together, newest month first."""
    rows = (
        _row("b1", Currency.BTC, OrderSide.BUY, 90, qty="1", price="100", fee="0"),
        _row("s1", Currency.BTC, OrderSide.SELL, 80, qty="1", price="130", fee="0"),
        _row("b2", Currency.ETH, OrderSide.BUY, 2, qty="1", price="50", fee="0"),
    )
    totals = month_subtotals(build_lots(rows), UTC)
    assert [(t.month, t.lots, t.cost, t.gain) for t in totals] == [
        ("2026-10", 1, _D(50), _D(0)),
        ("2026-07", 1, _D(100), _D(30)),
    ]


def test_the_screen_takes_every_tax_figure_from_the_tax_service() -> None:
    """Invariant: the screen shows the service's estimate as is; the brackets fill its base."""
    rows = (_row("b1", Currency.BTC, OrderSide.BUY, 10, fee="2.00"),)
    fake = _FakeService(_estimate())
    service = TaxScreenService(
        _Source(rows),
        scales={2026: SCALE_2026},
        taxpayer={},
        default_personal_minimum=_D(5550),
        service=fake,  # type: ignore[arg-type]
    )
    screen = service.tax_screen(as_of=_NOW, prices={"BTC": _D(200)})
    assert isinstance(screen, TaxScreen)
    assert screen.estimate.hermes_tax == TaxEstimate(amount=_D("123.45"))
    assert [f.filled for f in screen.brackets[:2]] == [_D(6000), _D(500)]
    assert screen.fees == _D("2.00")
    assert fake.calls[0]["prices"] == {"BTC": _D(200)}
    assert fake.calls[0]["taxpayer"] is None


def test_a_pending_estimate_is_passed_through() -> None:
    """Invariant: when the service has no estimate, the screen is its pending notice."""
    pending = TaxPending(label="tax pending: scale missing for 2026")
    service = TaxScreenService(
        _Source(()),
        scales={},
        taxpayer={},
        default_personal_minimum=_D(0),
        service=_FakeService(pending),  # type: ignore[arg-type]
    )
    assert service.tax_screen(as_of=_NOW, prices={}) == pending


def test_the_taxpayer_figures_of_the_year_reach_the_service() -> None:
    """Invariant: the year's taxpayer figures are the ones the estimate is asked with."""
    figures = TaxpayerYear(general_income=_D(30000), personal_minimum=_D(5550))
    fake = _FakeService(_estimate())
    TaxScreenService(
        _Source(()),
        scales={2026: SCALE_2026},
        taxpayer={2026: figures, 2025: TaxpayerYear()},
        default_personal_minimum=_D(5550),
        service=fake,  # type: ignore[arg-type]
    ).tax_screen(as_of=_NOW, prices={})
    assert fake.calls[0]["taxpayer"] is figures


def _two_month(rows: tuple[EurTradeRow, ...]) -> tuple[TwoMonthFlag, ...]:
    estimate = estimate_year(
        rows,
        year=2026,
        scales={2026: SCALE_2026},
        taxpayer=None,
        default_personal_minimum=_D(5550),
        prices={},
        as_of=_NOW,
        is_shadow=True,
    )
    assert isinstance(estimate, TaxYearEstimate)
    return estimate.two_month


def test_two_month_flags_name_a_coin_loss_with_a_nearby_buy() -> None:
    """Invariant: a SOL loss with a SOL buy three weeks later is flagged, with both dates."""
    rows = (
        _row("b1", Currency.SOL, OrderSide.BUY, 100, price="160", fee="0"),
        _row("s1", Currency.SOL, OrderSide.SELL, 80, price="140", fee="0"),
        _row("b2", Currency.SOL, OrderSide.BUY, 59, price="135", fee="0"),
    )
    flags = _two_month(rows)
    assert [(f.asset, f.sale_ref, f.loss) for f in flags] == [("SOL", "s1", _D(20))]
    assert flags[0].bought_on == date(2026, 8, 5)


def test_two_month_flags_never_name_usdt() -> None:
    """Invariant: a USDT loss with a USDT buy nearby (quote legs) raises no flag."""
    rows = (
        _row("seed", Currency.USDT, OrderSide.BUY, 100, qty="1000", price="0.95", quote_leg=True),
        _row("q1", Currency.USDT, OrderSide.SELL, 80, qty="500", price="0.90", quote_leg=True),
        _row("q2", Currency.USDT, OrderSide.BUY, 70, qty="300", price="0.91", quote_leg=True),
    )
    assert _two_month(rows) == ()


def test_a_flagged_sale_is_marked_on_its_lot() -> None:
    """Invariant: the screen marks the lots a flagged sale used, and only those."""
    rows = (
        _row("b1", Currency.SOL, OrderSide.BUY, 100, price="160", fee="0"),
        _row("s1", Currency.SOL, OrderSide.SELL, 80, price="140", fee="0"),
        _row("b2", Currency.SOL, OrderSide.BUY, 59, price="135", fee="0"),
    )
    screen = TaxScreenService(
        _Source(rows),
        scales={2026: SCALE_2026},
        taxpayer={},
        default_personal_minimum=_D(5550),
    ).tax_screen(as_of=_NOW, prices={"SOL": _D(150)})
    assert isinstance(screen, TaxScreen)
    assert {lot.ref: lot.two_month for lot in screen.lots} == {"b1": True, "b2": False}


def test_bracket_fills_of_a_one_band_scale_put_the_base_in_it() -> None:
    """Invariant: with a single open band, the whole base sits in it."""
    scale = SavingsScale(bands=(SCALE_2026.bands[-1],))
    (fill,) = bracket_fills(scale, _D(42))
    assert fill.filled == _D(42)
    assert fill.upper is None
