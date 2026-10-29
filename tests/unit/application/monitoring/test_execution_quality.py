"""Unit tests for the execution-quality monitor (#362) and its serve inputs (#1181).

The serve wiring is covered end to end in
``tests/integration/entrypoints/test_serve_execution_quality.py``.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.monitoring.execution_quality import (
    ExecutionObservation,
    ExecutionQualityMonitor,
    observation_of,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.monitoring.control_chart import ChartParameters
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_CID = "01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _normal(i: int) -> ExecutionObservation:
    jitter = (i % 5 - 2) * 0.5
    return ExecutionObservation(fee_bps=10.0 + jitter, slippage_bps=3.0 + jitter, latency_ms=200.0)


def _warm(monitor: ExecutionQualityMonitor) -> None:
    for i in range(30):
        assert monitor.observe(_normal(i), at=_AT, correlation_id=_CID) == []


def test_slippage_breach_emits_one_warning_for_that_metric() -> None:
    """A slippage jump emits one WARNING anomaly sourced to slippage only."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = []
    for i in range(20):
        obs = _normal(i)
        events += monitor.observe(
            ExecutionObservation(obs.fee_bps, 25.0, obs.latency_ms), at=_AT, correlation_id=_CID
        )
    assert [(e.source, e.severity) for e in events] == [
        ("execution_quality.slippage_bps", AnomalySeverity.WARNING)
    ]


def test_reason_names_metric_and_units_without_pnl() -> None:
    """The alert text names the metric in bps and carries no P&L wording (ruling 1)."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = monitor.observe(
        ExecutionObservation(fee_bps=60.0, slippage_bps=3.0, latency_ms=200.0),
        at=_AT,
        correlation_id=_CID,
    )
    reason = events[0].reason
    assert reason.startswith("fee_bps out of control")
    assert "bps" in reason
    assert not any(word in reason.lower() for word in ("pnl", "p&l", "profit", "equity", "sharpe"))


def test_latency_breach_is_independent_of_other_metrics() -> None:
    """Each metric has its own chart: a latency spike alerts only latency."""
    monitor = ExecutionQualityMonitor()
    _warm(monitor)
    events = monitor.observe(
        ExecutionObservation(fee_bps=10.0, slippage_bps=3.0, latency_ms=5_000.0),
        at=_AT,
        correlation_id=_CID,
    )
    assert [e.source for e in events] == ["execution_quality.latency_ms"]


def _quality(**overrides: Any) -> FillQualityRecorded:
    """Return a live-book quality row of 0.5 BTC filled at 2000 USDT, with ``overrides``."""
    fields: dict[str, Any] = {
        "occurred_at": _AT,
        "correlation_id": _CID,
        "strategy_id": StrategyId("swing-donchian-4h"),
        "pair": Pair(base=Currency.BTC, quote=Currency.USDT),
        "side": TradeSide.BUY,
        "qty": Decimal("0.5"),
        "book_source": BookSource.LIVE,
        "book_time": _AT,
        "fill_price": Decimal("2000"),
        "model_price": None,
        "drift_bps": None,
        "book_mid": Decimal("1999"),
        "slippage": Decimal("0.0012"),
        "arrival_price": Decimal("1998"),
        "delta_ms": 6_250,
    }
    fields.update(overrides)
    return FillQualityRecorded(**fields)


_FEE = Money(amount=Decimal("1.5"), currency=Currency.USDT)


def test_observation_of_a_fill_is_its_fee_slippage_and_latency_in_bps_and_ms() -> None:
    """A 1.5 USDT fee on 1000 USDT notional is 15 bps; 0.0012 slippage is 12 bps (#1181)."""
    assert observation_of(_quality(), _FEE) == ExecutionObservation(
        fee_bps=15.0, slippage_bps=12.0, latency_ms=6250.0
    )


def test_a_fee_outside_the_quote_currency_is_not_measured() -> None:
    """A fee in another currency cannot be set against the quote notional: ``None``, rest kept."""
    fee = Money(amount=Decimal("1.5"), currency=Currency.EUR)
    assert observation_of(_quality(), fee) == ExecutionObservation(
        fee_bps=None, slippage_bps=12.0, latency_ms=6250.0
    )


def test_a_fill_without_latency_has_no_latency_observation() -> None:
    """``delta_ms=None`` (clock before the bar close, #30 D-3) is not read as 0 ms."""
    assert observation_of(_quality(delta_ms=None), _FEE) == ExecutionObservation(
        fee_bps=15.0, slippage_bps=12.0, latency_ms=None
    )


def test_a_fill_without_a_book_mid_has_no_slippage_observation() -> None:
    """A one-sided book has no mid, so no slippage to chart; fee and latency stay."""
    assert observation_of(_quality(book_mid=None, slippage=None), _FEE) == ExecutionObservation(
        fee_bps=15.0, slippage_bps=None, latency_ms=6250.0
    )


def test_an_unmeasured_metric_does_not_move_its_chart() -> None:
    """A ``None`` latency is skipped: the baseline is the two measured values (#1181).

    Baseline size 2 over 100 and 300 ms: mean 200, sigma 141.42. Then 2000 ms:
    EWMA 0.2 * 2000 + 0.8 * 200 = 560 over 200 + 3 * 141.42 * 0.2 = 284.85, and
    CUSUM+ 1800 / 141.42 - 0.5 = 12.23 over 5. Read as 0 ms, the ``None`` would
    make the baseline 0 and 100 (mean 50, sigma 70.71), and 300 would already
    alert: EWMA 110 over 50 + 3 * 70.71 * 0.2 = 92.43.
    """
    monitor = ExecutionQualityMonitor(ChartParameters(baseline_size=2))
    for latency in (None, 100.0, 300.0):
        assert (
            monitor.observe(
                ExecutionObservation(fee_bps=None, slippage_bps=None, latency_ms=latency),
                at=_AT,
                correlation_id=_CID,
            )
            == []
        )
    (event,) = monitor.observe(
        ExecutionObservation(fee_bps=None, slippage_bps=None, latency_ms=2_000.0),
        at=_AT,
        correlation_id=_CID,
    )
    assert event.reason == (
        "latency_ms out of control (ewma_high, cusum_high): 2000.00 ms against a baseline "
        "of 200.00 ± 141.42 ms"
    )


def test_priming_alerts_nothing_even_over_a_breach() -> None:
    """Replaying past fills, a step included, returns no event and raises nothing (#1181)."""
    monitor = ExecutionQualityMonitor(ChartParameters(baseline_size=4))
    past = [ExecutionObservation(None, s, None) for s in (4.0, 6.0, 4.0, 6.0, 12.0)]
    monitor.prime(past)
    after = monitor.observe(ExecutionObservation(None, 12.0, None), at=_AT, correlation_id=_CID)
    assert after == []


_SLIPPAGES = st.lists(
    st.floats(min_value=0.0, max_value=50.0, allow_nan=False), min_size=0, max_size=60
)


@settings(max_examples=50)
@given(past=_SLIPPAGES, live=_SLIPPAGES)
def test_a_primed_monitor_alerts_exactly_as_one_that_saw_every_fill_live(
    past: list[float], live: list[float]
) -> None:
    """Priming with the past then observing live gives the live run's events (#1181).

    This is what lets a restart neither forget the baseline nor alert an
    episode a second time.
    """
    params = ChartParameters(baseline_size=5)
    continuous = ExecutionQualityMonitor(params)
    for value in past:
        continuous.observe(ExecutionObservation(None, value, None), at=_AT, correlation_id=_CID)
    restarted = ExecutionQualityMonitor(params)
    restarted.prime(ExecutionObservation(None, value, None) for value in past)

    def run(monitor: ExecutionQualityMonitor) -> list[str]:
        return [
            e.reason
            for value in live
            for e in monitor.observe(
                ExecutionObservation(None, value, None), at=_AT, correlation_id=_CID
            )
        ]

    assert run(restarted) == run(continuous)
