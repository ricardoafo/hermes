"""Unit tests for shadow-parity metrics M-09 and M-10, computed blind (#360)."""

from __future__ import annotations

import logging

import pytest

from hermes.application.calibration.shadow_parity import (
    MetricStatus,
    MetricVerdict,
    SealedValueError,
    m09_pnl_correlation,
    m10_sharpe_match,
)

_TRADES = [12.0, -4.0, 7.5, -2.0, 9.0, -6.0, 3.0, 11.0, -1.5, 4.0] * 4
"""40 matched trades with a varied P&L path."""


def _drift(values: list[float], bias: float) -> list[float]:
    return [v + bias * ((i % 3) - 1) for i, v in enumerate(values)]


class TestM09:
    """Correlation of cumulative P&L, shadow against replay (ADR-011 D-19)."""

    def test_identical_runs_pass(self) -> None:
        """A replay that reproduces the shadow trades exactly passes."""
        verdict = m09_pnl_correlation(_TRADES, list(_TRADES), seed=1, resamples=300)
        assert verdict.status is MetricStatus.OK

    # SEV-1: a fill model that diverges from live must block certification (ADR-011 D-19)
    def test_unrelated_runs_block(self) -> None:
        """A replay whose P&L path runs opposite to the shadow blocks."""
        replay = [-v for v in _TRADES]
        verdict = m09_pnl_correlation(_TRADES, replay, seed=1, resamples=300)
        assert verdict.status is MetricStatus.BLOCK

    def test_small_noise_still_passes(self) -> None:
        """Small per-trade differences keep the cumulative curves correlated."""
        verdict = m09_pnl_correlation(_TRADES, _drift(_TRADES, 0.5), seed=1, resamples=300)
        assert verdict.status is MetricStatus.OK

    def test_fewer_than_thirty_trades_is_insufficient(self) -> None:
        """SA-5: below 30 matched trades the metric is not evaluated."""
        verdict = m09_pnl_correlation(_TRADES[:29], _TRADES[:29], seed=1, resamples=50)
        assert verdict.status is MetricStatus.INSUFFICIENT

    def test_a_flat_shadow_curve_is_insufficient(self) -> None:
        """A shadow with zero P&L on every trade has a flat curve: r is undefined (#1179)."""
        verdict = m09_pnl_correlation([0.0] * 40, _TRADES, seed=1, resamples=50)
        assert verdict.status is MetricStatus.INSUFFICIENT

    def test_same_seed_same_verdict(self) -> None:
        """The bootstrap is reproducible for a seed."""
        a = m09_pnl_correlation(_TRADES, _drift(_TRADES, 3.0), seed=9, resamples=200)
        b = m09_pnl_correlation(_TRADES, _drift(_TRADES, 3.0), seed=9, resamples=200)
        assert (a.status, a.unseal(certified=True)) == (b.status, b.unseal(certified=True))

    def test_unmatched_lengths_raise(self) -> None:
        """Trades must be matched one to one."""
        with pytest.raises(ValueError, match="matched"):
            m09_pnl_correlation(_TRADES, _TRADES[:-1], seed=1)


class TestM10:
    """Gap between 30-day annualised Sharpe of shadow and replay (ADR-011 D-19)."""

    _RETURNS = [0.004, -0.002, 0.003, 0.001, -0.001] * 8

    def test_identical_returns_pass(self) -> None:
        """Zero gap passes."""
        assert m10_sharpe_match(self._RETURNS, list(self._RETURNS)).status is MetricStatus.OK

    # SEV-1: a Sharpe mismatch between live and backtest must block (ADR-011 D-19)
    def test_large_gap_blocks(self) -> None:
        """Shadow returns much weaker than the replay's block."""
        weaker = [r - 0.003 for r in self._RETURNS]
        assert m10_sharpe_match(weaker, self._RETURNS).status is MetricStatus.BLOCK

    def test_under_thirty_days_is_insufficient(self) -> None:
        """Without a 30-day window there is no Sharpe to compare."""
        verdict = m10_sharpe_match(self._RETURNS[:29], self._RETURNS[:29])
        assert verdict.status is MetricStatus.INSUFFICIENT

    def test_mismatched_days_raise(self) -> None:
        """Both series must cover the same days."""
        with pytest.raises(ValueError, match="same days"):
            m10_sharpe_match(self._RETURNS, self._RETURNS[:-1])


class TestBlindness:
    """Values are sealed: only the status leaves a verdict before certification."""

    # SEV-1: ruling 1 / ADR-012 D-06 — no correlation or Sharpe value before certification
    def test_repr_str_and_logs_carry_status_only(self, caplog: pytest.LogCaptureFixture) -> None:
        """repr, str and a logged verdict show the status but never the value."""
        verdict = MetricVerdict("M-10", MetricStatus.BLOCK, 0.4242)
        with caplog.at_level(logging.INFO):
            logging.getLogger("test").info("verdict %s / %r", verdict, verdict)
        rendered = f"{verdict} {verdict!r} {caplog.text}"
        assert "block" in rendered
        assert "0.4242" not in rendered
        assert "0.42" not in rendered

    # SEV-1: ruling 1 — the raw value is refused before certification
    def test_unseal_refuses_before_certification(self) -> None:
        """Reading the value without certification raises; after, it is returned."""
        verdict = MetricVerdict("M-09", MetricStatus.OK, 0.93)
        with pytest.raises(SealedValueError, match="sealed until certification"):
            verdict.unseal(certified=False)
        assert verdict.unseal(certified=True) == 0.93
