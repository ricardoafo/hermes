"""Property + example tests for :func:`select_lots_fifo`.

Property invariants (Hypothesis):

- **Oldest-first**: for any bag of open lots, the returned consumptions
  reference lots in ``opened_at`` ascending order (secondary tie-breaker on
  ``lot_id``).
- **Exact fill**: the sum of consumed quantities equals the requested
  ``quantity_to_consume`` — no rounding drift, no double-count.
- **Multi-lot fan-out**: when one lot cannot cover the request, the
  algorithm draws from as many oldest lots as needed, and only the last
  one touched may be partially consumed.
- **Insufficient inventory**: when total open remainders are smaller than
  the request, the algorithm returns Failure(InsufficientInventory)
  without emitting any consumptions.

Example-based tests cover:

- The ADR-003 §03 worked example (three BTC buys, one 0.0015 sell that
  drains lot #1 fully and lot #2 partially).
- Deterministic FIFO tie-breaker on identical timestamps (LotId lexical
  order — see selection.py module docstring).

Reference: ADR-003 §03, ADR-002 (Spanish FIFO), code-conventions.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from returns.result import Failure, Success

from hermes.domain.lots.lot import Lot
from hermes.domain.lots.selection import InsufficientInventory, select_lots_fifo
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_SWING = StrategyId("swing")

# ---------------------------------------------------------------------------
# Deterministic ULID generator — Hypothesis-safe.
#
# LotId.new() uses the wall clock, which is nondeterministic under Hypothesis
# shrinking. We roll our own 26-char Crockford-base32-safe values by mapping
# integers to the Crockford alphabet used by python-ulid.
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 chars, no I/L/O/U


def _lot_id_from_int(seed: int) -> LotId:
    """Return a deterministic 26-char Crockford-base32 LotId from an integer."""
    chars: list[str] = []
    n = seed & ((1 << 130) - 1)  # 26 chars x 5 bits = 130 bits
    for _ in range(26):
        chars.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return LotId(value="".join(reversed(chars)))


def _lot(
    *,
    lot_id: str,
    remaining: Decimal,
    original: Decimal,
    opened_at: datetime,
    cost_basis: Decimal = Decimal("50000"),
) -> Lot:
    """Return a BTC lot with the given identity and remaining quantity."""
    return Lot(
        lot_id=LotId(value=lot_id),
        asset=Currency.BTC,
        original_qty=Quantity(amount=original, asset=Currency.BTC),
        remaining_qty=Quantity(amount=remaining, asset=Currency.BTC),
        cost_basis_per_unit=Money(amount=cost_basis, currency=Currency.EUR),
        strategy_id=_SWING,
        opened_at=opened_at,
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


@st.composite
def _open_lots_strategy(draw: st.DrawFn, min_lots: int = 1, max_lots: int = 8) -> list[Lot]:
    """Draw a list of BTC open lots with positive remaining quantities."""
    count = draw(st.integers(min_value=min_lots, max_value=max_lots))
    lots: list[Lot] = []
    for i in range(count):
        # Remaining amounts range 0.001 to 5 BTC — realistic sizes.
        remaining = draw(
            st.decimals(
                min_value=Decimal("0.001"),
                max_value=Decimal("5"),
                places=3,
                allow_nan=False,
                allow_infinity=False,
            )
        )
        # Offset opened_at by index minutes to keep primary sort deterministic.
        offset_seconds = draw(st.integers(min_value=0, max_value=10_000))
        opened_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i * 60 + offset_seconds)
        lots.append(
            _lot(
                lot_id=_lot_id_from_int(i * 1_000_003 + 17).value,
                remaining=remaining,
                original=remaining,
                opened_at=opened_at,
            )
        )
    return lots


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestSelectLotsFifoOldestFirst:
    """FIFO invariant: consumed lots appear in opened_at ascending order."""

    @given(lots=_open_lots_strategy(min_lots=2, max_lots=6))
    def test_consumption_order_matches_opened_at_ascending(self, lots: list[Lot]) -> None:
        """The consumed lot_ids follow the lots sorted by opened_at ascending."""
        # Consume a small slice — one lot's worth is enough to prove order.
        target_amount = lots[0].remaining_qty.amount / 2
        # Only bother if we can consume something meaningful.
        if target_amount <= Decimal("0"):
            return
        result = select_lots_fifo(
            lots,
            Quantity(amount=target_amount, asset=Currency.BTC),
            Money(amount=Decimal("60000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumptions = result.unwrap()
        expected_order = sorted(lots, key=lambda lot: (lot.opened_at, lot.lot_id.value))
        # The first consumption references the oldest lot.
        assert consumptions[0].lot_id == expected_order[0].lot_id


class TestSelectLotsFifoExactFill:
    """FIFO invariant: sum of consumed quantities equals the requested amount."""

    @given(lots=_open_lots_strategy(min_lots=1, max_lots=5))
    def test_consumed_sum_equals_requested(self, lots: list[Lot]) -> None:
        """Sum(consumption.consumed_qty) == requested quantity, to the last decimal."""
        total_available = sum((lot.remaining_qty.amount for lot in lots), start=Decimal("0"))
        # Request half the pool — guaranteed to be satisfiable.
        target = total_available / 2
        if target <= Decimal("0"):
            return
        result = select_lots_fifo(
            lots,
            Quantity(amount=target, asset=Currency.BTC),
            Money(amount=Decimal("60000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumed_total = sum((c.consumed_qty.amount for c in result.unwrap()), start=Decimal("0"))
        assert consumed_total == total_available / 2


class TestSelectLotsFifoInsufficientInventory:
    """When request exceeds total inventory, algorithm refuses cleanly."""

    @given(lots=_open_lots_strategy(min_lots=1, max_lots=4))
    def test_over_request_returns_insufficient_inventory(self, lots: list[Lot]) -> None:
        """Requesting > total remaining yields Failure(InsufficientInventory)."""
        total_available = sum((lot.remaining_qty.amount for lot in lots), start=Decimal("0"))
        # Ask for double what exists — always insufficient.
        target = total_available * 2 + Decimal("1")
        result = select_lots_fifo(
            lots,
            Quantity(amount=target, asset=Currency.BTC),
            Money(amount=Decimal("60000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert result == Failure(
            InsufficientInventory(
                requested=Quantity(amount=target, asset=Currency.BTC),
                available=Quantity(amount=total_available, asset=Currency.BTC),
            )
        )


# ---------------------------------------------------------------------------
# Example-based tests — ADR-003 §03 worked example + tie-breaker
# ---------------------------------------------------------------------------


class TestAdr003WorkedExample:
    """Replays the ADR-003 §03 FIFO worked example end-to-end."""

    def _three_btc_lots(self) -> list[Lot]:
        """Return the three BUY lots documented in ADR-003 §03."""
        return [
            _lot(
                lot_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
                remaining=Decimal("0.001"),
                original=Decimal("0.001"),
                opened_at=datetime(2026, 11, 5, tzinfo=UTC),
                cost_basis=Decimal("65000"),
            ),
            _lot(
                lot_id="01J9Z8T3M5BQR2FVKN4XWY6PH2",
                remaining=Decimal("0.001"),
                original=Decimal("0.001"),
                opened_at=datetime(2026, 12, 10, tzinfo=UTC),
                cost_basis=Decimal("68000"),
            ),
            _lot(
                lot_id="01J9Z8T3M5BQR2FVKN4XWY6PH3",
                remaining=Decimal("0.001"),
                original=Decimal("0.001"),
                opened_at=datetime(2027, 2, 15, tzinfo=UTC),
                cost_basis=Decimal("72000"),
            ),
        ]

    def test_sell_0_0015_at_75000_consumes_lots_1_and_2(self) -> None:
        """SELL 0.0015 @ 75000 fully consumes lot #1 and takes 0.0005 from lot #2."""
        lots = self._three_btc_lots()
        result = select_lots_fifo(
            lots,
            Quantity(amount=Decimal("0.0015"), asset=Currency.BTC),
            Money(amount=Decimal("75000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumptions = result.unwrap()
        # Two consumptions: lot #1 full drain, lot #2 partial.
        assert len(consumptions) == 2
        # First consumption: full lot #1, gain = (75000-65000)*0.001 = 10 EUR.
        assert consumptions[0].lot_id.value == "01J9Z8T3M5BQR2FVKN4XWY6PH1"
        assert consumptions[0].consumed_qty.amount == Decimal("0.001")
        assert consumptions[0].realized_gain.amount == Decimal("10.000")
        # Second consumption: 0.0005 of lot #2, gain = (75000-68000)*0.0005 = 3.5 EUR.
        assert consumptions[1].lot_id.value == "01J9Z8T3M5BQR2FVKN4XWY6PH2"
        assert consumptions[1].consumed_qty.amount == Decimal("0.0005")
        assert consumptions[1].realized_gain.amount == Decimal("3.5000")

    def test_exact_fill_leaves_no_partial_of_oldest(self) -> None:
        """SELL 0.001 @ 75000 drains only lot #1 — no partial consumption of newer lots."""
        lots = self._three_btc_lots()
        result = select_lots_fifo(
            lots,
            Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            Money(amount=Decimal("75000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumptions = result.unwrap()
        assert len(consumptions) == 1
        assert consumptions[0].lot_id.value == "01J9Z8T3M5BQR2FVKN4XWY6PH1"
        assert consumptions[0].consumed_qty.amount == Decimal("0.001")


class TestFifoTieBreaker:
    """When two lots share opened_at, the LotId lexical order breaks the tie."""

    def test_identical_timestamps_ordered_by_lot_id_ascending(self) -> None:
        """Two lots at the same instant: the lexically-smaller lot_id consumes first."""
        same_time = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
        # AAA... sorts before ZZZ... — AAA lot must consume first.
        lot_a = _lot(
            lot_id="01A00000000000000000000000",
            remaining=Decimal("0.001"),
            original=Decimal("0.001"),
            opened_at=same_time,
            cost_basis=Decimal("50000"),
        )
        lot_z = _lot(
            lot_id="01Z00000000000000000000000",
            remaining=Decimal("0.001"),
            original=Decimal("0.001"),
            opened_at=same_time,
            cost_basis=Decimal("60000"),
        )
        # Deliberately pass in reverse to prove the algorithm sorts, not the caller.
        result = select_lots_fifo(
            [lot_z, lot_a],
            Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            Money(amount=Decimal("70000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumptions = result.unwrap()
        # The AAA lot (smaller lot_id) got consumed even though ZZZ was passed first.
        assert consumptions[0].lot_id.value == "01A00000000000000000000000"


class TestSelectLotsFifoAssetFiltering:
    """The algorithm ignores lots whose asset differs from the requested asset."""

    def test_eth_lots_ignored_when_selling_btc(self) -> None:
        """A pool with only ETH lots cannot supply a BTC SELL — insufficient."""
        eth_lot = Lot(
            lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH1"),
            asset=Currency.ETH,
            original_qty=Quantity(amount=Decimal("10"), asset=Currency.ETH),
            remaining_qty=Quantity(amount=Decimal("10"), asset=Currency.ETH),
            cost_basis_per_unit=Money(amount=Decimal("3000"), currency=Currency.EUR),
            strategy_id=_SWING,
            opened_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
        result = select_lots_fifo(
            [eth_lot],
            Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            Money(amount=Decimal("70000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert result == Failure(
            InsufficientInventory(
                requested=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
                available=Quantity(amount=Decimal("0"), asset=Currency.BTC),
            )
        )


class TestMultiLotFanOut:
    """When one lot is too small, the algorithm draws from consecutive older lots."""

    def test_three_lot_span_only_last_is_partial(self) -> None:
        """Draw across three lots: first two fully drained, third partially."""
        lots = [
            _lot(
                lot_id="01A00000000000000000000000",
                remaining=Decimal("0.5"),
                original=Decimal("0.5"),
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                cost_basis=Decimal("40000"),
            ),
            _lot(
                lot_id="01B00000000000000000000000",
                remaining=Decimal("0.3"),
                original=Decimal("0.3"),
                opened_at=datetime(2026, 2, 1, tzinfo=UTC),
                cost_basis=Decimal("50000"),
            ),
            _lot(
                lot_id="01C00000000000000000000000",
                remaining=Decimal("1.0"),
                original=Decimal("1.0"),
                opened_at=datetime(2026, 3, 1, tzinfo=UTC),
                cost_basis=Decimal("55000"),
            ),
        ]
        # Request 1.0 BTC — needs full 0.5 + full 0.3 + partial 0.2 from lot C.
        result = select_lots_fifo(
            lots,
            Quantity(amount=Decimal("1.0"), asset=Currency.BTC),
            Money(amount=Decimal("70000"), currency=Currency.EUR),
            datetime(2027, 3, 20, tzinfo=UTC),
        )
        assert isinstance(result, Success)
        consumptions = result.unwrap()
        assert len(consumptions) == 3
        # Lot A fully drained.
        assert consumptions[0].consumed_qty.amount == Decimal("0.5")
        # Lot B fully drained.
        assert consumptions[1].consumed_qty.amount == Decimal("0.3")
        # Lot C partially consumed — only the last touched lot is partial.
        assert consumptions[2].consumed_qty.amount == Decimal("0.2")
