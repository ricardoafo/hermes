"""One replay of a backtest funnel grid: the base, a neighbour or the cost stress.

A replay overrides parameters of whatever strategy the backtest driver loads
and scales whatever execution model it builds, so it does not depend on which
strategy runs. Pre-registration v2 fixes its grid in
``scripts/backtest/funnel_evidence.py``; a research batch candidate states its
own (ADR-026 §1, :meth:`~hermes.application.calibration.research_batch.Candidate.funnel_replays`).

Reference: openspec/archive/v2-funnel-evidence/change.md; ADR-026 §1.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from hermes.application.execution.prereg_book import ExecutionModel
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "BASE_TRIAL_ID",
    "COST_STRESS",
    "COST_STRESS_MULTIPLIER",
    "FeesAt",
    "FunnelReplay",
    "neighbour",
]

COST_STRESS_MULTIPLIER: Final = 2
"""Pre-registration v2 §6.2 and ADR-021 §5: every fee and slippage times 2."""

BASE_TRIAL_ID: Final = "base"

FeesAt = Callable[[datetime, Pair], FeeSchedule]


@dataclass(frozen=True, slots=True)
class FunnelReplay:
    """One replay of the funnel grid.

    Attributes:
        trial_id: The id of its series in the evidence and of its trial line.
        overrides: Strategy parameters it replaces; empty for the base and
            the cost stress.
        cost_multiplier: Factor on every execution bps and fee bps.
    """

    trial_id: str
    overrides: Mapping[str, Any] = field(default_factory=dict)
    cost_multiplier: int = 1

    def parameters(self, base: Mapping[str, Any]) -> dict[str, Any]:
        """Return the base strategy parameters with this replay's overrides.

        Args:
            base: The parameters of the loaded strategy.

        Returns:
            A new mapping; ``base`` is not changed.

        Raises:
            ValueError: An override names a parameter ``base`` does not have,
                or sets it to the base value (change D-2).
        """
        for name, value in self.overrides.items():
            if name not in base:
                raise ValueError(f"neighbour {self.trial_id}: the strategy has no {name}")
            if base[name] == value:
                raise ValueError(f"neighbour {self.trial_id} equals the base {name}={base[name]}")
        return {**base, **self.overrides}

    def model(self, base: ExecutionModel) -> ExecutionModel:
        """Return ``base`` with every ``*_bps`` cost times ``cost_multiplier``.

        Args:
            base: The execution model the driver built.

        Returns:
            ``base`` itself when the multiplier is 1.
        """
        if self.cost_multiplier == 1:
            return base
        scaled = {
            f.name: getattr(base, f.name) * self.cost_multiplier
            for f in fields(base)
            if f.name.endswith("_bps")
        }
        return replace(base, **scaled)

    def fees(self, fees_at: FeesAt) -> FeesAt:
        """Return a fee lookup whose every tier's bps are times ``cost_multiplier``.

        Args:
            fees_at: The fee lookup of the loaded history.

        Returns:
            ``fees_at`` itself when the multiplier is 1.
        """
        if self.cost_multiplier == 1:
            return fees_at
        multiplier = self.cost_multiplier

        def stressed(at: datetime, pair: Pair) -> FeeSchedule:
            schedule = fees_at(at, pair)
            tiers = tuple(
                replace(t, maker_bps=t.maker_bps * multiplier, taker_bps=t.taker_bps * multiplier)
                for t in schedule.tiers
            )
            return replace(schedule, tiers=tiers)

        return stressed

    def trial_fields(self) -> dict[str, Any]:
        """Return what this replay adds to its trial line's parameters (change D-4)."""
        if self.trial_id == BASE_TRIAL_ID:
            return {}
        extra: dict[str, Any] = {"funnel.replay": self.trial_id}
        if self.cost_multiplier != 1:
            extra["execution.cost_multiplier"] = self.cost_multiplier
        return extra


def neighbour(name: str, value: int | Decimal) -> FunnelReplay:
    """Return the replay that moves ``name`` alone to ``value``; its trial id is ``name=value``."""
    return FunnelReplay(trial_id=f"{name}={value}", overrides={name: value})


COST_STRESS: Final = FunnelReplay(
    trial_id=f"cost_stress={COST_STRESS_MULTIPLIER}", cost_multiplier=COST_STRESS_MULTIPLIER
)
