"""SizingCalculator — position sizing for Swing MVP1 per pre-registration §5.4/§5.5.

Every limit is a share of the fixed experiment capital (pre-reg §2), never of
current equity:

.. code-block:: text

    risk_budget    = risk_pct * capital                      # §5.4: 1% → 50 USDT
    risk_notional  = risk_budget ÷ (entry - stop) * entry    # §5.4
    position_cap   = max_position_pct * capital              # §5.4: 5% → 250 USDT
    notional       = min(risk_notional, position_cap)
    exposure_cap   = max_total_exposure_pct * capital        # §5.5: 15% → 750 USDT
    blocked when open_notional + notional > exposure_cap
    qty            = notional ÷ entry

Fractional Kelly is not applied: §5.4 defers it until ≥ 30 OOS trades exist,
which needs a new pre-registration rather than a parameter here.

Divisions round toward zero, so the sized notional never exceeds the exact
formula and the realised risk ``notional * (entry - stop) ÷ entry`` never
exceeds the risk budget, without an epsilon.

Reference: pre-registration-v1.md §2, §5.4, §5.5;
openspec/archive/sizing-per-prereg/change.md (supersedes spec-3 D-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, localcontext

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot

__all__ = [
    "SizingCalculator",
    "SizingInput",
    "SizingPolicy",
    "SizingResult",
]

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class SizingPolicy:
    """Pre-registered sizing limits, all relative to the experiment capital.

    Built from ``config/strategies.yaml`` (``capital_budget`` and the
    strategy's ``sizing`` block) — never from literals in code.

    Args:
        capital: Fixed experiment capital in the quote currency (§2). > 0.
        risk_pct: Share of capital risked per trade (§5.4). In (0, 1].
        max_position_pct: Notional cap per position as a share of capital
            (§5.4). In (0, 1].
        max_total_exposure_pct: Cap on the summed notional of all open
            positions as a share of capital (§5.5). In (0, 1].
    """

    capital: Decimal
    risk_pct: Decimal
    max_position_pct: Decimal
    max_total_exposure_pct: Decimal

    def __post_init__(self) -> None:
        """Reject a non-positive capital or a share outside (0, 1].

        Raises:
            ValueError: On any out-of-range field.
        """
        if self.capital <= _ZERO:
            raise ValueError(f"SizingPolicy.capital must be > 0, got {self.capital!r}")
        for name, val in [
            ("risk_pct", self.risk_pct),
            ("max_position_pct", self.max_position_pct),
            ("max_total_exposure_pct", self.max_total_exposure_pct),
        ]:
            if not _ZERO < val <= _ONE:
                raise ValueError(f"SizingPolicy.{name} must be in (0, 1], got {val!r}")

    @property
    def risk_budget(self) -> Decimal:
        """Quote amount one trade may lose at its stop (§5.4)."""
        return self.risk_pct * self.capital

    @property
    def position_cap(self) -> Decimal:
        """Largest notional a single position may have (§5.4)."""
        return self.max_position_pct * self.capital

    @property
    def exposure_cap(self) -> Decimal:
        """Largest summed notional across open positions (§5.5)."""
        return self.max_total_exposure_pct * self.capital


@dataclass(frozen=True, slots=True)
class SizingInput:
    """Per-signal inputs to position sizing.

    Args:
        entry_px: Intended entry price (quote per base unit). > 0.
        stop_px: Initial chandelier stop of the entry signal. > 0; a stop at
            or above the entry fails sizing (long-only, §5.1).
        open_notional: Summed notional of the positions already open, in the
            quote currency, from ``PortfolioTracker.open_notional()``. ≥ 0.
    """

    entry_px: Decimal
    stop_px: Decimal
    open_notional: Decimal

    def __post_init__(self) -> None:
        """Validate prices are positive and open notional is not negative.

        Raises:
            ValueError: On a non-positive price or a negative open notional.
        """
        for name, val in [("entry_px", self.entry_px), ("stop_px", self.stop_px)]:
            if val <= _ZERO:
                raise ValueError(f"SizingInput.{name} must be > 0, got {val!r}")
        if self.open_notional < _ZERO:
            raise ValueError(f"SizingInput.open_notional must be >= 0, got {self.open_notional!r}")


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Outcome of a successful sizing computation.

    Args:
        qty: Base-asset quantity to trade, ``notional ÷ entry`` rounded down.
        notional: Quote-currency notional of the position.
        risk_notional: The §5.4 risk formula before the hard cap.
        position_cap: The §5.4 per-position cap.
        binding_cap: ``"risk"`` when the risk formula set the size,
            ``"position_cap"`` when the hard cap did.
    """

    qty: Decimal
    notional: Decimal
    risk_notional: Decimal
    position_cap: Decimal
    binding_cap: str


class SizingCalculator:
    """Stateless position-sizing calculator bound to one :class:`SizingPolicy`.

    Args:
        policy: Pre-registered limits from the strategy's configuration.
    """

    def __init__(self, policy: SizingPolicy) -> None:
        """Bind the calculator to ``policy``."""
        self._policy = policy

    def compute(self, inp: SizingInput) -> Result[SizingResult, DomainError]:
        """Size one long entry per pre-registration §5.4 and §5.5.

        Args:
            inp: Entry price, stop and the notional already open.

        Returns:
            ``Success(SizingResult)``, or ``Failure(InvalidLot)`` when the stop
            is not below the entry, when the position would push open notional
            past the §5.5 exposure cap, or when the size rounds to zero.
        """
        policy = self._policy
        stop_distance = inp.entry_px - inp.stop_px
        if stop_distance <= _ZERO:
            return Failure(
                InvalidLot(
                    reason=(
                        f"SizingCalculator: stop {inp.stop_px!r} is not below entry "
                        f"{inp.entry_px!r}; a long entry needs a positive stop distance."
                    )
                )
            )

        with localcontext() as ctx:
            # Round toward zero so neither notional nor qty exceeds the exact formula.
            ctx.rounding = ROUND_DOWN
            risk_notional = policy.risk_budget * inp.entry_px / stop_distance
            position_cap = policy.position_cap
            notional = min(risk_notional, position_cap)
            qty = notional / inp.entry_px

        if inp.open_notional + notional > policy.exposure_cap:
            return Failure(
                InvalidLot(
                    reason=(
                        f"SizingCalculator: total exposure cap — open notional "
                        f"{inp.open_notional!r} + new {notional!r} exceeds "
                        f"{policy.exposure_cap!r} (pre-reg §5.5)."
                    )
                )
            )

        if qty <= _ZERO:
            return Failure(
                InvalidLot(
                    reason=(
                        f"SizingCalculator: computed qty is zero "
                        f"(notional={notional!r}, entry_px={inp.entry_px!r})."
                    )
                )
            )

        return Success(
            SizingResult(
                qty=qty,
                notional=notional,
                risk_notional=risk_notional,
                position_cap=position_cap,
                binding_cap="risk" if risk_notional <= position_cap else "position_cap",
            )
        )
