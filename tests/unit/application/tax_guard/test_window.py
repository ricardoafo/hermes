"""Tax-guard rolling-window computation + breach checks.

Each test protects one invariant of the ADR-002 §Decision 6
recharacterization guard contract:

- Streams that cross a threshold FIRE an ``AnomalyDetected``.
- Streams that do NOT cross a threshold do NOT fire.
- The guard is a pure function: no clock reads, no I/O, no bus writes.
- Equality-at-threshold does NOT fire (ADR-002 wording is strict ``>``).
- The 90-day boundary is inclusive at both ends (closed interval).

Property tests via ``hypothesis`` pin the "cross vs. no-cross" invariant
across randomly-generated event streams so a subtle off-by-one in the
threshold check surfaces here rather than in production.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.application.tax_guard import (
    WindowStats,
    check_breach,
    compute_window,
    evaluate,
)
from hermes.application.tax_guard.window import TAX_GUARD_SOURCE
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.lot_closed import LotClosed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.models import TaxGuardConfig

# ---------------------------------------------------------------------------
# Fixtures — deterministic anchor + config so property tests derandomise
# ---------------------------------------------------------------------------

_ANCHOR = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"


def _default_config(
    *,
    trade_count_max: int = 500,
    distinct_assets_max: int = 12,
    fee_income_eur_max: str = "0",
    window_days: int = 90,
) -> TaxGuardConfig:
    """Return a TaxGuardConfig with ADR-002 §6 defaults; override any single knob."""
    return TaxGuardConfig(
        annual_realised_pnl_alert_eur=Decimal("1000"),
        wash_sale_window_days=30,
        max_position_pct_portfolio=Decimal("0.25"),
        recharacterization_window_days=window_days,
        trade_count_90d_max=trade_count_max,
        distinct_assets_90d_max=distinct_assets_max,
        fee_income_90d_max_eur=Decimal(fee_income_eur_max),
    )


def _trade(
    *,
    at: datetime,
    base: Currency = Currency.BTC,
    quote: Currency = Currency.USDT,
    fee_amount: str = "0.10",
    fee_currency: Currency = Currency.USDT,
    side: TradeSide = TradeSide.BUY,
) -> TradeExecuted:
    """Return a deterministic TradeExecuted at ``at`` with the given pair + fee."""
    pair = Pair(base=base, quote=quote)
    return TradeExecuted(
        occurred_at=at,
        correlation_id=_CID,
        pair=pair,
        side=side,
        qty=Quantity(amount=Decimal("0.01"), asset=base),
        price=Price(amount=Decimal("50000"), pair=pair),
        fee=Money(amount=Decimal(fee_amount), currency=fee_currency),
        strategy_id=StrategyId("swing"),
    )


def _lot_closed(*, at: datetime) -> LotClosed:
    """Return a LotClosed event — used to verify non-trade events are ignored."""
    return LotClosed(
        occurred_at=at,
        correlation_id=_CID,
        lot_id=LotId(value="01J9Z8T3M5BQR2FVKN4XWY6PH2"),
        consumed_qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
        realized_gain=Money(amount=Decimal("13"), currency=Currency.EUR),
    )


# ---------------------------------------------------------------------------
# compute_window — window inclusion + purity
# ---------------------------------------------------------------------------


class TestComputeWindowInclusion:
    """Verify the 90-day window covers the correct interval."""

    def test_event_inside_window_is_counted(self) -> None:
        """A trade 30 days before ``now`` is inside the 90-day window and counted."""
        events = [_trade(at=_ANCHOR - timedelta(days=30))]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 1

    def test_event_exactly_at_window_start_is_included(self) -> None:
        """The window boundary is inclusive: a trade at t-90d contributes to counters."""
        boundary = _ANCHOR - timedelta(days=90)
        events = [_trade(at=boundary)]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 1

    def test_event_exactly_at_now_is_included(self) -> None:
        """The window end is inclusive: a trade at ``now`` contributes to counters."""
        events = [_trade(at=_ANCHOR)]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 1

    def test_event_strictly_before_window_start_is_excluded(self) -> None:
        """A trade one microsecond before t-90d falls outside the closed interval."""
        outside = _ANCHOR - timedelta(days=90, microseconds=1)
        events = [_trade(at=outside)]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 0

    def test_event_after_now_is_excluded(self) -> None:
        """Future events (after ``now``) never contribute — protects replay scenarios."""
        events = [_trade(at=_ANCHOR + timedelta(minutes=1))]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 0


class TestComputeWindowMetrics:
    """Verify counters aggregate correctly across the observed trade stream."""

    def test_distinct_assets_derives_from_pair_base(self) -> None:
        """distinct_assets counts unique pair.base — same asset on 2 markets = 1."""
        events = [
            _trade(at=_ANCHOR - timedelta(days=1), base=Currency.BTC, quote=Currency.USDT),
            _trade(at=_ANCHOR - timedelta(days=2), base=Currency.BTC, quote=Currency.USDC),
            _trade(at=_ANCHOR - timedelta(days=3), base=Currency.ETH, quote=Currency.USDT),
        ]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.distinct_assets == 2

    def test_fees_bucketed_by_currency(self) -> None:
        """fees_by_currency sums per-currency; different currencies are kept separate."""
        events = [
            _trade(at=_ANCHOR - timedelta(days=1), fee_amount="1.5", fee_currency=Currency.USDT),
            _trade(at=_ANCHOR - timedelta(days=2), fee_amount="0.5", fee_currency=Currency.USDT),
            _trade(at=_ANCHOR - timedelta(days=3), fee_amount="2.0", fee_currency=Currency.EUR),
        ]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.fees_by_currency[Currency.USDT] == Decimal("2.0")
        assert stats.fees_by_currency[Currency.EUR] == Decimal("2.0")

    def test_eur_fee_income_returns_zero_when_no_eur_fees(self) -> None:
        """When no EUR fees exist, eur_fee_income() returns Decimal(0) not KeyError."""
        events = [
            _trade(at=_ANCHOR - timedelta(days=1), fee_amount="1", fee_currency=Currency.USDT),
        ]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.eur_fee_income() == Decimal("0")

    def test_non_trade_events_are_ignored(self) -> None:
        """LotClosed events do not contribute to trade_count / distinct_assets / fees."""
        events = [_lot_closed(at=_ANCHOR - timedelta(days=1))]
        stats = compute_window(events, _ANCHOR, window_days=90)
        assert stats.trade_count == 0
        assert stats.distinct_assets == 0
        assert stats.fees_by_currency == {}


class TestComputeWindowInvariants:
    """Verify purity + input-validation invariants of the window builder."""

    def test_returns_frozen_stats(self) -> None:
        """WindowStats is a frozen dataclass — mutation raises."""
        stats = compute_window([], _ANCHOR, window_days=90)
        with pytest.raises((AttributeError, TypeError)):
            stats.trade_count = 999  # type: ignore[misc]

    def test_naive_now_raises_value_error(self) -> None:
        """A naive ``now`` violates code-conventions §9 (UTC-internal) — reject fast."""
        naive = datetime(2026, 8, 22, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            compute_window([], naive, window_days=90)

    def test_zero_window_days_raises_value_error(self) -> None:
        """window_days must be positive; zero is a config bug — reject fast."""
        with pytest.raises(ValueError, match="positive"):
            compute_window([], _ANCHOR, window_days=0)

    def test_input_sequence_is_not_mutated(self) -> None:
        """The window builder never mutates or re-sorts its input sequence."""
        original = [
            _trade(at=_ANCHOR - timedelta(days=5)),
            _trade(at=_ANCHOR - timedelta(days=1)),
        ]
        snapshot = list(original)
        compute_window(original, _ANCHOR, window_days=90)
        # Length + per-position identity: nothing dropped, nothing reordered.
        assert len(original) == 2
        assert original[0] is snapshot[0]
        assert original[1] is snapshot[1]


# ---------------------------------------------------------------------------
# check_breach — threshold semantics
# ---------------------------------------------------------------------------


class TestCheckBreachNoFire:
    """Verify clean windows return None."""

    def test_empty_stats_do_not_fire(self) -> None:
        """A zero-trade window is clean — no anomaly emitted."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=0,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        result = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert result is None
        assert stats.trade_count == 0

    def test_trade_count_at_threshold_does_not_fire(self) -> None:
        """ADR-002 wording is strict ``>``: exactly-at-limit is clean."""
        config = _default_config()
        stats = WindowStats(
            trade_count=config.trade_count_90d_max,
            distinct_assets=0,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        result = check_breach(stats, config, now=_ANCHOR, correlation_id=_CID)
        assert result is None
        assert stats.trade_count == config.trade_count_90d_max

    def test_distinct_assets_at_threshold_does_not_fire(self) -> None:
        """distinct_assets == max is still clean; only strictly greater fires."""
        config = _default_config()
        stats = WindowStats(
            trade_count=0,
            distinct_assets=config.distinct_assets_90d_max,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        result = check_breach(stats, config, now=_ANCHOR, correlation_id=_CID)
        assert result is None
        assert stats.distinct_assets == config.distinct_assets_90d_max

    def test_eur_fees_at_threshold_do_not_fire(self) -> None:
        """EUR fees exactly at the configured ceiling do not fire the guard."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=0,
            fees_by_currency={Currency.EUR: Decimal("5")},
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        config = _default_config(fee_income_eur_max="5")
        result = check_breach(stats, config, now=_ANCHOR, correlation_id=_CID)
        assert result is None
        assert stats.eur_fee_income() == Decimal("5")

    def test_non_eur_fees_do_not_trip_eur_threshold(self) -> None:
        """USDT / USDC fees do not contribute to the EUR threshold check."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=0,
            fees_by_currency={Currency.USDT: Decimal("1000")},
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        result = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert result is None
        assert stats.eur_fee_income() == Decimal("0")


class TestCheckBreachFires:
    """Verify a breach produces a well-formed AnomalyDetected."""

    def test_trade_count_over_threshold_fires(self) -> None:
        """trade_count > ceiling emits AnomalyDetected sourced at tax_guard.recharacterization."""
        stats = WindowStats(
            trade_count=501,
            distinct_assets=0,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert isinstance(anomaly, AnomalyDetected)
        assert anomaly.source == TAX_GUARD_SOURCE
        assert anomaly.severity == AnomalySeverity.CRITICAL
        assert "trade_count=501" in anomaly.reason

    def test_distinct_assets_over_threshold_fires(self) -> None:
        """distinct_assets > ceiling fires and names the metric in the reason."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=13,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert isinstance(anomaly, AnomalyDetected)
        assert "distinct_assets=13" in anomaly.reason

    def test_eur_fee_income_over_threshold_fires(self) -> None:
        """Any EUR fee above the ceiling fires — ADR-002 §6 sets that ceiling at 0."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=0,
            fees_by_currency={Currency.EUR: Decimal("0.01")},
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert isinstance(anomaly, AnomalyDetected)
        assert "fee_income_eur=0.01" in anomaly.reason

    def test_multiple_breaches_are_all_reported(self) -> None:
        """When several thresholds cross, every one appears in the reason string."""
        stats = WindowStats(
            trade_count=600,
            distinct_assets=15,
            fees_by_currency={Currency.EUR: Decimal("10")},
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert isinstance(anomaly, AnomalyDetected)
        assert "trade_count=600" in anomaly.reason
        assert "distinct_assets=15" in anomaly.reason
        assert "fee_income_eur=10" in anomaly.reason

    def test_anomaly_carries_caller_correlation_id(self) -> None:
        """The event's correlation_id is the caller-supplied ULID, never invented internally."""
        stats = WindowStats(
            trade_count=999,
            distinct_assets=0,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id="CUSTOM-CID")
        assert isinstance(anomaly, AnomalyDetected)
        assert anomaly.correlation_id == "CUSTOM-CID"

    def test_naive_now_raises_value_error(self) -> None:
        """check_breach rejects naive ``now`` — protects the audit trail."""
        stats = WindowStats(
            trade_count=0,
            distinct_assets=0,
            window_start=_ANCHOR - timedelta(days=90),
            window_end=_ANCHOR,
        )
        naive = datetime(2026, 8, 22, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            check_breach(stats, _default_config(), now=naive, correlation_id=_CID)


# ---------------------------------------------------------------------------
# evaluate — the composed public entry point
# ---------------------------------------------------------------------------


class TestEvaluateComposition:
    """Verify evaluate() composes compute_window + check_breach faithfully."""

    def test_breaching_stream_returns_anomaly(self) -> None:
        """A stream with 13 distinct assets (> 12) returns a CRITICAL AnomalyDetected."""
        # 6 currencies allowlisted; we cannot literally have 13 distinct assets, so
        # exercise the trade-count axis instead: 501 trades against a max of 500.
        events = [_trade(at=_ANCHOR - timedelta(hours=i)) for i in range(501)]
        anomaly = evaluate(events, _default_config(), now=_ANCHOR, correlation_id=_CID)
        assert isinstance(anomaly, AnomalyDetected)
        assert anomaly.severity == AnomalySeverity.CRITICAL


# ---------------------------------------------------------------------------
# Property tests — cross vs. no-cross invariants
# ---------------------------------------------------------------------------


_HYPOTHESIS = settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@_HYPOTHESIS
@given(count=st.integers(min_value=0, max_value=500))
def test_property_trade_count_at_or_below_threshold_never_fires(count: int) -> None:
    """For any count in [0, 500], the guard stays silent — inclusive lower boundary."""
    config = _default_config()
    stats = WindowStats(
        trade_count=count,
        distinct_assets=0,
        window_start=_ANCHOR - timedelta(days=90),
        window_end=_ANCHOR,
    )
    result = check_breach(stats, config, now=_ANCHOR, correlation_id=_CID)
    assert result is None
    assert count <= config.trade_count_90d_max


@_HYPOTHESIS
@given(count=st.integers(min_value=501, max_value=100_000))
def test_property_trade_count_above_threshold_always_fires(count: int) -> None:
    """For any count > 500, the guard fires — exclusive upper boundary."""
    stats = WindowStats(
        trade_count=count,
        distinct_assets=0,
        window_start=_ANCHOR - timedelta(days=90),
        window_end=_ANCHOR,
    )
    anomaly = check_breach(stats, _default_config(), now=_ANCHOR, correlation_id=_CID)
    assert isinstance(anomaly, AnomalyDetected)
    assert anomaly.source == TAX_GUARD_SOURCE


@_HYPOTHESIS
@given(days_ago=st.integers(min_value=91, max_value=365))
def test_property_events_outside_window_never_contribute(days_ago: int) -> None:
    """Events older than window_days never affect trade_count."""
    events = [_trade(at=_ANCHOR - timedelta(days=days_ago))]
    stats = compute_window(events, _ANCHOR, window_days=90)
    assert stats.trade_count == 0


@_HYPOTHESIS
@given(days_ago=st.integers(min_value=0, max_value=90))
def test_property_events_inside_window_always_contribute(days_ago: int) -> None:
    """Every event inside the closed [t-90d, t] interval is counted exactly once."""
    events = [_trade(at=_ANCHOR - timedelta(days=days_ago))]
    stats = compute_window(events, _ANCHOR, window_days=90)
    assert stats.trade_count == 1


@_HYPOTHESIS
@given(
    inside=st.integers(min_value=0, max_value=200),
    outside=st.integers(min_value=0, max_value=200),
)
def test_property_only_inside_events_are_counted(inside: int, outside: int) -> None:
    """Mixing ``inside`` in-window and ``outside`` out-of-window trades counts ``inside``."""
    events = [_trade(at=_ANCHOR - timedelta(days=1, minutes=i)) for i in range(inside)] + [
        _trade(at=_ANCHOR - timedelta(days=200, minutes=i)) for i in range(outside)
    ]
    stats = compute_window(events, _ANCHOR, window_days=90)
    assert stats.trade_count == inside


@_HYPOTHESIS
@given(assets=st.sets(st.sampled_from(list(Currency)), min_size=0, max_size=6))
def test_property_distinct_assets_matches_unique_bases(assets: set[Currency]) -> None:
    """distinct_assets equals the cardinality of the unique base-currency set observed."""
    events = []
    for i, asset in enumerate(assets):
        # Avoid pair validation errors: quote must differ from base. Rotate through
        # a stable non-base currency for each event.
        quote = Currency.USDT if asset != Currency.USDT else Currency.USDC
        events.append(
            _trade(
                at=_ANCHOR - timedelta(days=1, minutes=i),
                base=asset,
                quote=quote,
            )
        )
    stats = compute_window(events, _ANCHOR, window_days=90)
    assert stats.distinct_assets == len(assets)
