"""Position views computed from ledger facts (change ``ledger-read-model`` D-4 to D-7).

Invariants:

- the money identities hold for any priced position: ``net = gross - fees paid
  - fees to close`` on the detail and the row alike, ``cost`` is the EUR
  acquisition value of the held quantity, and ``now worth - cost = gross -
  fees paid``;
- any missing EUR input (no rate, an unpriced lot, lots that do not cover the
  size, no lots) makes the row EUR pending with no EUR money figure;
- the stop distance is measured from the current price, in percent and in ATR.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.console.position_figures import LotFacts, PositionFacts, build_detail
from hermes.application.console.views import TaxPending

_T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
_RATE = Decimal("0.8")
_TOLERANCE = Decimal("1e-18")


def _lot(remaining: str = "0.5", original: str = "0.5", *, priced: bool = True) -> LotFacts:
    return LotFacts(
        remaining_qty=Decimal(remaining),
        original_qty=Decimal(original),
        opened_at=_T0 + timedelta(seconds=5),
        price_eur=Decimal(40000) if priced else None,
        fee_eur=Decimal(20) if priced else None,
    )


def _facts(
    *, lots: tuple[LotFacts, ...] = (), rate: Decimal | None = _RATE, size: str = "0.5"
) -> PositionFacts:
    return PositionFacts(
        strategy="swing-donchian-4h",
        coin="BTC",
        timeframe="4h",
        size=Decimal(size),
        cost_native=Decimal(50000) * Decimal(size),
        current_price=Decimal(52000),
        stop_price=Decimal(48000),
        atr=Decimal(1000),
        signal_at=_T0,
        lots=lots,
        price_path=(Decimal(50000), Decimal(53000), Decimal(52000)),
        eur_per_usdt=rate,
    )


def test_worked_example_net() -> None:
    """0.5 BTC: gross 800, fee 20, fees to close 20.8, net 759.2 (tax pending)."""
    detail = build_detail(_facts(lots=(_lot(),)))
    assert (detail.gross, detail.fees_paid, detail.fees_to_close, detail.net) == (
        Decimal(800),
        Decimal(20),
        Decimal("20.8"),
        Decimal("759.2"),
    )


def test_row_net_equals_detail_net() -> None:
    """The list row and the detail never disagree on net."""
    detail = build_detail(_facts(lots=(_lot(),)))
    assert detail.row.net == detail.net


def test_tax_is_pending_until_the_tax_module() -> None:
    """No tax estimate is invented; net is before tax."""
    assert isinstance(build_detail(_facts(lots=(_lot(),))).tax, TaxPending)


def test_change_is_measured_in_eur() -> None:
    """Change % compares the EUR entry with the EUR price now: 40,000 to 41,600 is 4%."""
    assert build_detail(_facts(lots=(_lot(),))).row.change_pct == Decimal(4)


def test_stop_distance_in_atr_and_percent() -> None:
    """52,000 over a 48,000 stop with ATR 1,000 is 4 ATR and 4000/52000 of the price."""
    row = build_detail(_facts(lots=(_lot(),))).row
    assert (row.stop_distance_atr, row.stop_distance_pct) == (
        Decimal(4),
        Decimal(400000) / Decimal(52000),
    )


def test_high_since_entry_includes_the_entry_price() -> None:
    """With every mark below entry, the high is the entry price itself."""
    facts = _facts(lots=(_lot(),))
    low = PositionFacts(**{**_as_kwargs(facts), "price_path": (Decimal(49000),)})
    assert build_detail(low).row.high_since_entry == Decimal(50000)


def test_opened_at_is_the_earliest_lot() -> None:
    """The position opened at its first fill, not at the signal."""
    late = LotFacts(
        Decimal("0.2"), Decimal("0.2"), _T0 + timedelta(hours=8), Decimal(1), Decimal(0)
    )
    early = _lot("0.3", "0.3")
    row = build_detail(_facts(lots=(late, early))).row
    assert row.opened_at == early.opened_at


def test_entry_values_carry_the_atr_in_eur() -> None:
    """The entry signal's ATR is shown at the read rate: 1,000 USDT is 800 EUR."""
    assert build_detail(_facts(lots=(_lot(),))).entry_values == (("atr", Decimal(800)),)


@pytest.mark.parametrize(
    ("lots", "rate"),
    [
        ((), _RATE),
        ((_lot(priced=False),), _RATE),
        ((_lot("0.4", "0.4"),), _RATE),
        ((_lot(),), None),
    ],
    ids=["no-lots", "unpriced-lot", "lots-short-of-size", "no-rate"],
)
def test_missing_eur_input_makes_the_row_pending(
    lots: tuple[LotFacts, ...], rate: Decimal | None
) -> None:
    """Without every EUR input the row is pending and carries no EUR money."""
    detail = build_detail(_facts(lots=lots, rate=rate))
    assert (detail.row.eur_pending, detail.row.net, detail.gross, detail.entry_values) == (
        True,
        Decimal(0),
        Decimal(0),
        (),
    )


def test_pending_row_change_is_measured_in_usdt() -> None:
    """With no EUR values the change still shows, from the USDT prices: 50,000 to 52,000."""
    assert build_detail(_facts()).row.change_pct == Decimal(4)


def test_pending_timeline_does_not_show_a_eur_fill_price() -> None:
    """An unpriced fill is labelled pending rather than given a EUR price."""
    detail = build_detail(_facts(lots=(_lot(priced=False),)))
    assert [e.label for e in detail.timeline] == [
        "signal: long BTC on 4h close",
        "filled 0.5 · EUR price pending",
    ]


@pytest.mark.parametrize("field", ["size", "cost_native", "current_price", "atr"])
def test_facts_reject_non_positive_values(field: str) -> None:
    """A held position always has a positive size, cost, price and ATR."""
    kwargs = {**_as_kwargs(_facts()), field: Decimal(0)}
    with pytest.raises(ValueError, match=field):
        PositionFacts(**kwargs)


def _as_kwargs(facts: PositionFacts) -> dict[str, object]:
    return {name: getattr(facts, name) for name in PositionFacts.__slots__}


_AMOUNT = st.decimals(min_value=Decimal("0.01"), max_value=Decimal(100000), places=2)
_QTY = st.decimals(min_value=Decimal("0.001"), max_value=Decimal(10), places=3)


@st.composite
def _priced_positions(draw: st.DrawFn) -> PositionFacts:
    lots = []
    for i in range(draw(st.integers(min_value=1, max_value=4))):
        original = draw(_QTY)
        remaining = draw(st.decimals(min_value=Decimal("0.001"), max_value=original, places=3))
        lots.append(
            LotFacts(
                remaining_qty=remaining,
                original_qty=original,
                opened_at=_T0 + timedelta(hours=i),
                price_eur=draw(_AMOUNT),
                fee_eur=draw(st.decimals(min_value=Decimal(0), max_value=Decimal(50), places=2)),
            )
        )
    size = sum((lot.remaining_qty for lot in lots), Decimal(0))
    current = draw(_AMOUNT)
    return PositionFacts(
        strategy="s",
        coin="BTC",
        timeframe="4h",
        size=size,
        cost_native=draw(_AMOUNT) * size,
        current_price=current,
        stop_price=current * Decimal("0.9"),
        atr=draw(_AMOUNT),
        signal_at=_T0,
        lots=tuple(lots),
        price_path=(current,),
        eur_per_usdt=draw(st.decimals(min_value=Decimal("0.5"), max_value=Decimal(2), places=4)),
    )


@settings(max_examples=200)
@given(facts=_priced_positions())
def test_property_net_is_gross_less_fees(facts: PositionFacts) -> None:
    """For any priced position, the detail's net is gross - fees paid - fees to close."""
    detail = build_detail(facts)
    assert detail.row.net == detail.gross - detail.fees_paid - detail.fees_to_close


@settings(max_examples=200)
@given(facts=_priced_positions())
def test_property_cost_is_the_acquisition_value_of_what_is_held(facts: PositionFacts) -> None:
    """Cost = sum(price_eur x remaining) + sum(fee_eur x remaining / original)."""
    expected = sum(
        (
            lot.price_eur * lot.remaining_qty + lot.fee_eur * lot.remaining_qty / lot.original_qty
            for lot in facts.lots
            if lot.price_eur is not None and lot.fee_eur is not None
        ),
        Decimal(0),
    )
    assert abs(build_detail(facts).cost - expected) <= _TOLERANCE * max(expected, Decimal(1))


@settings(max_examples=200)
@given(facts=_priced_positions())
def test_property_now_worth_less_cost_is_gross_less_fees_paid(facts: PositionFacts) -> None:
    """The figures the detail shows side by side reconcile: worth - cost = gross - fees paid."""
    detail = build_detail(facts)
    gap = (detail.now_worth - detail.cost) - (detail.gross - detail.fees_paid)
    assert abs(gap) <= _TOLERANCE * max(detail.now_worth, Decimal(1))
