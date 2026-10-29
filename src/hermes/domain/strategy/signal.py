"""StrategySignal — value object carrying a single trading signal.

A ``StrategySignal`` is emitted by a ``StrategyPort`` adapter when an entry
or exit condition fires.  It is immutable (frozen dataclass) and carries
everything the OrderRouter needs to size and route the order.

Fields are deliberately flat — no nested objects — so the router can
destructure without import-chain gymnastics.

Reference: code-conventions.md §1 (Value Object).
spec-5-swing-strategy-adapter change.md D-2, R-7.
pre-registration-v1.md §5.3 (market entry), §5.4 (sizing hint).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

__all__ = ["OpenPosition", "SignalKind", "SignalSide", "StrategySignal"]


class SignalSide(StrEnum):
    """Direction of the signal.

    Only LONG is supported in MVP1 (pre-reg §5.1 long-only).
    SHORT is reserved for future use.
    """

    LONG = "long"
    SHORT = "short"


class SignalKind(StrEnum):
    """Whether this signal opens or closes a position."""

    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Immutable trading signal emitted by a strategy adapter.

    Attributes:
        kind: ``entry`` opens a new position; ``exit`` closes one.
        side: Direction — ``long`` or ``short``.  MVP1 emits ``long`` only
            (pre-reg §5.1).
        entry_price: Bar close price of the signal bar.  Set on ``entry``
            signals; ``None`` on pure ``exit`` signals.
        stop_price: Chandelier trailing-stop level.  Always set; for entry
            signals it is the initial stop used for sizing.  Invariant:
            ``stop_price < entry_price`` for long entries (R-7).
        atr_context: ATR(14) value on the signal bar used for risk sizing.
            Always > 0 (R-7).
        signal_time: ``close_time`` of the bar that triggered this signal.
            UTC-aware.
        exit_reason: Human-readable exit reason (e.g. ``"chandelier"``,
            ``"time_stop"``, ``"regime_break"``).  ``None`` for entry signals.
        f_star_estimate: Kelly fraction estimate.  Always ``None`` until OD-4
            threshold (≥30 OOS trades) is met (D-2).

    Reference: pre-registration-v1.md §5.3, §5.4.
    """

    kind: SignalKind
    side: SignalSide
    entry_price: Decimal | None
    stop_price: Decimal
    atr_context: Decimal
    signal_time: datetime
    exit_reason: str | None = None
    f_star_estimate: Decimal | None = None

    def __post_init__(self) -> None:
        """Enforce signal invariants.

        Raises:
            ValueError: When ``entry_price`` is missing on entry signals,
                when ``stop_price`` ≥ ``entry_price`` for long entries,
                or when ``atr_context`` ≤ 0.
        """
        if self.kind == SignalKind.ENTRY and self.entry_price is None:
            raise ValueError("StrategySignal: entry_price must be set for ENTRY signals")
        if (
            self.kind == SignalKind.ENTRY
            and self.side == SignalSide.LONG
            and self.entry_price is not None
            and self.stop_price >= self.entry_price
        ):
            raise ValueError(
                f"StrategySignal: stop_price ({self.stop_price}) must be < "
                f"entry_price ({self.entry_price}) for long ENTRY signals"
            )
        if self.atr_context <= Decimal("0"):
            raise ValueError(f"StrategySignal: atr_context must be > 0, got {self.atr_context}")
        if self.signal_time.tzinfo is None:
            raise ValueError("StrategySignal: signal_time must be UTC-aware")


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """A position a strategy already holds, restored at warm-up.

    The swing strategy decides from an entry time and price (the exit stop
    comes from its bar buffers), so that is what a restart must carry over.
    ``last_stop`` is the stop recorded after the last handled bar (#388); a
    stop rule that depends on the previous stop reads it after a restart.

    Attributes:
        entry_time: ``close_time`` of the entry bar. UTC-aware.
        entry_price: Entry reference price. Always > 0.
        last_stop: Stop in force after the last handled bar, or ``None``
            when none is recorded for this position.

    Raises:
        ValueError: On a naive ``entry_time`` or a non-positive ``entry_price``.

    Reference: 309-serve-bar-loop change.md D-6; 388-stop-marks change.md D-4.
    """

    entry_time: datetime
    entry_price: Decimal
    last_stop: Decimal | None = None

    def __post_init__(self) -> None:
        """Enforce the position invariants."""
        if self.entry_time.tzinfo is None:
            raise ValueError("OpenPosition: entry_time must be UTC-aware")
        if self.entry_price <= Decimal("0"):
            raise ValueError(f"OpenPosition: entry_price must be > 0, got {self.entry_price}")
