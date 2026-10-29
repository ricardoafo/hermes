"""The tax screen and Modelo 100 flag the same repurchase sales (issue #1171).

Both surfaces once carried their own two-month rule: the tax screen counted
Madrid calendar dates, Modelo 100 counted instants. A loss sale with a buy
late on the window's last Madrid day was flagged by one and not the other.
The Code Owner picked the Madrid-date rule on 18 Oct; this guard fails the
moment the two surfaces drift apart again.

Reference: IRPF art. 33.5.f; ADR-016 decisions 2 and 8.
"""

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord, build_modelo100
from hermes.domain.tax.two_month import Acquisition, two_month_flags
from hermes.domain.tax.year_position import Disposal
from hermes.domain.value_objects.currency import Currency

_YEAR = 2026
# Madrid-year 2026 in UTC: 00:00 CET on 1 Jan to 23:59 CET on 31 Dec.
_YEAR_START = datetime(2025, 12, 31, 23, 0, tzinfo=UTC)
_YEAR_END = datetime(2026, 12, 31, 22, 59, tzinfo=UTC)
_LOT_AT = datetime(2025, 1, 1, tzinfo=UTC)
_ASSETS = st.sampled_from((Currency.BTC, Currency.ETH))
# Offsets that put a buy around a sale's window edges, where a Madrid-date
# rule and an instant rule part ways.
_EDGE_DAYS = st.sampled_from((-62, -61, -60, -59, 59, 60, 61, 62))


@dataclass(frozen=True, slots=True)
class _Sale:
    ref: str
    asset: Currency
    at: datetime
    proceeds: Decimal
    cost: Decimal
    fee: Decimal

    @property
    def lot(self) -> str:
        return f"lot-{self.ref}"


@dataclass(frozen=True, slots=True)
class _Ledger:
    sales: tuple[_Sale, ...]
    buys: tuple[PurchaseRecord, ...]


def _minute_in(draw: st.DrawFn, start: datetime, end: datetime) -> datetime:
    span = int((end - start).total_seconds() // 60)
    return start + timedelta(minutes=draw(st.integers(0, span)))


@st.composite
def _ledgers(draw: st.DrawFn) -> _Ledger:
    sales = tuple(
        _Sale(
            ref=f"s{i}",
            asset=draw(_ASSETS),
            at=_minute_in(draw, _YEAR_START, _YEAR_END),
            proceeds=Decimal(draw(st.integers(1, 5000))),
            cost=Decimal(draw(st.integers(1, 5000))),
            fee=Decimal(draw(st.integers(0, 50))),
        )
        for i in range(draw(st.integers(1, 8)))
    )
    buys: list[PurchaseRecord] = []
    for _ in range(draw(st.integers(0, 10))):
        anchor = draw(st.sampled_from(sales)).at
        offset = timedelta(days=draw(_EDGE_DAYS), minutes=draw(st.integers(-1440, 1440)))
        buys.append(
            PurchaseRecord(ref=f"b{len(buys)}", asset=draw(_ASSETS), acquired_at=anchor + offset)
        )
    for _ in range(draw(st.integers(0, 6))):
        at = _minute_in(draw, datetime(2025, 10, 1, tzinfo=UTC), datetime(2027, 3, 1, tzinfo=UTC))
        buys.append(PurchaseRecord(ref=f"b{len(buys)}", asset=draw(_ASSETS), acquired_at=at))
    # Each sale draws from its own lot; that lot is what was sold, so neither
    # surface may count it as a repurchase.
    buys += [PurchaseRecord(ref=s.lot, asset=s.asset, acquired_at=_LOT_AT) for s in sales]
    return _Ledger(sales=sales, buys=tuple(buys))


def _months_on(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _expected_flags(ledger: _Ledger) -> set[str]:
    """Apply art. 33.5.f by hand: a loss sale, another same-coin buy, two Madrid months."""
    madrid = ZoneInfo("Europe/Madrid")
    flagged: set[str] = set()
    for sale in ledger.sales:
        if sale.proceeds - sale.fee - sale.cost >= 0:
            continue
        sold_on = sale.at.astimezone(madrid).date()
        if any(
            buy.asset == sale.asset
            and buy.ref != sale.lot
            and _months_on(sold_on, -2)
            <= buy.acquired_at.astimezone(madrid).date()
            <= _months_on(sold_on, 2)
            for buy in ledger.buys
        ):
            flagged.add(sale.ref)
    return flagged


@settings(max_examples=300)
@given(_ledgers())
def test_tax_screen_and_modelo100_flag_the_same_sales(ledger: _Ledger) -> None:
    """For any ledger, both surfaces flag exactly the loss sales the Madrid-date rule names."""
    pieces = [
        DisposalPiece(
            sell_ref=s.ref,
            asset=s.asset,
            received=Currency.USDT,
            disposed_at=s.at,
            buy_ref=s.lot,
            acquired_at=_LOT_AT,
            qty=Decimal(1),
            proceeds_eur=s.proceeds,
            cost_eur=s.cost,
            fee_eur=s.fee,
        )
        for s in ledger.sales
    ]
    disposals = [
        Disposal(
            asset=s.asset.value,
            ref=s.ref,
            at=s.at,
            gain=s.proceeds - s.fee - s.cost,
            consumed_refs=frozenset({s.lot}),
        )
        for s in ledger.sales
    ]
    acquisitions = [
        Acquisition(asset=b.asset.value, ref=b.ref, at=b.acquired_at) for b in ledger.buys
    ]

    screen = {flag.sale_ref for flag in two_month_flags(disposals, acquisitions)}
    filing = {flag.sell_ref for flag in build_modelo100(_YEAR, pieces, ledger.buys).flags}

    expected = _expected_flags(ledger)
    assert screen == expected
    assert filing == expected
