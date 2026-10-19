"""Shadow-phase Sharpe kill: §10 "Shadow Sharpe < 0.3 for 60 consecutive days".

Pre-registration v1 §10 kills the strategy in shadow when its Sharpe stays
below 0.3 for 60 consecutive days. It does not name the Sharpe's window or
scale. The Code Owner fixed both on 2026-10-03 (decisions journal entry
``2026-10-03-calibration-metrics-and-shadow-sharpe-kill``):

- a 30-day rolling Sharpe on daily returns,
- annualised with √365 (crypto trades every day),
- evaluated once per UTC day; a day counts toward the streak only while that
  Sharpe is strictly below the floor.

A day at or above the floor, a day without enough data for the 30-day
window, or a missing day resets the streak. The kill fires on the 60th
consecutive day below the floor.

This is the shadow rule only. The live-phase rule ("30 rolling trades OOS
Sharpe < 0.3", a single evaluation) stays in
:class:`~hermes.application.strategy_protocol.kill_trigger_evaluator.KillTriggerEvaluator`.

The decision's reason never contains the Sharpe value: automation stays
blind to strategy outcomes before certification (ADR-012 D-06, binding
rule 4 of the 2026-10-03 shadow-P&L decision).

Reference: pre-registration-v1.md §10; issue #422.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from hermes.application.strategy_protocol.kill_trigger_evaluator import KillDecision
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.statistics.sharpe import sharpe_ratio

__all__ = [
    "ANNUALISATION_DAYS",
    "ROLLING_WINDOW_DAYS",
    "ShadowSharpeKill",
    "rolling_annualised_sharpe",
]

ROLLING_WINDOW_DAYS = 30
"""Code Owner ruling 2026-10-03: the §10 shadow Sharpe is a 30-day rolling Sharpe."""

ANNUALISATION_DAYS = 365
"""Code Owner ruling 2026-10-03: annualise daily Sharpe with √365."""


def rolling_annualised_sharpe(daily_returns: Sequence[float]) -> float | None:
    """Return the annualised Sharpe of the last 30 daily returns.

    Args:
        daily_returns: Daily shadow returns, oldest first.

    Returns:
        The annualised Sharpe, or ``None`` with fewer than 30 returns or zero
        dispersion (not enough evidence to count a day).
    """
    if len(daily_returns) < ROLLING_WINDOW_DAYS:
        return None
    per_day = sharpe_ratio(list(daily_returns[-ROLLING_WINDOW_DAYS:]))
    return None if per_day is None else per_day * math.sqrt(ANNUALISATION_DAYS)


@dataclass
class ShadowSharpeKill:
    """Counts consecutive UTC days with the rolling Sharpe below the §10 floor."""

    floor: float
    required_days: int
    _streak: int = field(default=0, init=False)
    _last_day: date | None = field(default=None, init=False)

    @classmethod
    def from_pre_registration(cls, pre_reg: PreRegistration) -> ShadowSharpeKill:
        """Build the rule from the signed §10 values (0.3, 60 days)."""
        triggers = pre_reg.kill_triggers()
        return cls(
            floor=triggers.rolling_sharpe_min,
            required_days=triggers.shadow_sharpe_consecutive_days,
        )

    @property
    def streak(self) -> int:
        """Consecutive days below the floor so far."""
        return self._streak

    def observe(self, day: date, rolling_sharpe: float | None) -> KillDecision:
        """Record one UTC day's rolling Sharpe and say whether the kill fires.

        Args:
            day: The UTC day evaluated. Days must be strictly increasing.
            rolling_sharpe: That day's annualised 30-day Sharpe, or ``None``
                when there is not enough data yet.

        Returns:
            A triggered :class:`KillDecision` once the streak reaches the
            required days, else an untriggered one.

        Raises:
            ValueError: ``day`` is not after the previous day observed.
        """
        if self._last_day is not None and day <= self._last_day:
            raise ValueError(f"days must increase: {day} after {self._last_day}")
        consecutive = self._last_day is None or day == self._last_day + timedelta(days=1)
        self._last_day = day
        below = rolling_sharpe is not None and rolling_sharpe < self.floor
        self._streak = (self._streak + 1 if consecutive else 1) if below else 0
        if self._streak >= self.required_days:
            return KillDecision(
                triggered=True,
                reason=(
                    f"Shadow Sharpe below the {self.floor:g} floor for "
                    f"{self.required_days} consecutive days (pre-reg §10)."
                ),
                rule="shadow_sharpe",
            )
        return KillDecision(triggered=False, reason="")
