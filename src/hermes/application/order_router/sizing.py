"""SizingCalculator — T-1 position-sizing composition for Swing MVP1.

Implements the three-cap ``min()`` formula from pre-registration-v1.md §5.4:

.. code-block:: python

    atr_notional   = (equity * risk_pct) / stop_atr_mult   # ATR-baseline (SA-7)
    kelly_notional = equity * min(0.25 * f_star_estimate, 0.05)  # Kelly cap (SA-1)
    hard_cap       = 0.05 * equity                          # §5.4 absolute 5% cap
    notional       = min(atr_notional, kelly_notional, hard_cap)
    qty            = notional / entry_px

All three caps are computed independently and the minimum is taken. This is
order-insensitive and verifiable as a property: ``sized ≤ cap_i ∀ i``.

MVP1 note: ``f_star_estimate`` is set to ``Decimal("0.20")`` by the composition
root (OD-4 defers live Kelly estimation until ≥ 30 OOS trades). With this value,
``kelly_notional = equity * 0.05``, which equals the hard cap — effectively the
hard cap always wins. The formula remains correct for future live values.

Reference: pre-registration-v1.md §5.4 (5% hard cap, supersedes SA-7 10%),
SA-7 (ATR-baseline), SA-1 (Kelly cap), spec-3-order-router change.md D-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot

__all__ = [
    "SizingCalculator",
    "SizingInput",
    "SizingResult",
]

# Pre-reg §5.4 binding constants — DO NOT change without updating the signed document.
_HARD_CAP_FRACTION: Decimal = Decimal("0.05")  # 5% of equity per position
_KELLY_FRACTION_MULTIPLIER: Decimal = Decimal("0.25")  # Kelly fraction = 0.25 * f_star
_KELLY_FLOOR_FRACTION: Decimal = Decimal("0.05")  # Kelly cap floor = 5% of equity


@dataclass(frozen=True, slots=True)
class SizingInput:
    """Immutable input bundle for T-1 position sizing.

    Args:
        equity: Total portfolio equity in the quote currency (e.g. USDT).
            Must be > 0.
        entry_px: Intended entry price (quote currency per base unit).
            Must be > 0.
        atr: Average True Range value in quote currency. Used in the
            ATR-baseline formula: ``notional = (equity * risk_pct) / stop_atr_mult``.
            Must be > 0.
        stop_atr_mult: ATR multiplier for the stop distance. Pre-reg §5.4
            default is ``Decimal("1.5")``. Must be > 0.
        risk_pct: Fraction of equity to risk per trade. Pre-reg §5.4
            default is ``Decimal("0.01")`` (1%). Must be in (0, 1].
        f_star_estimate: Kelly fraction estimate. MVP1 composition root
            uses ``Decimal("0.20")`` (OD-4 floor). Must be > 0.

    Reference: pre-registration-v1.md §5.4, spec-3-order-router change.md D-2.
    """

    equity: Decimal
    entry_px: Decimal
    atr: Decimal
    stop_atr_mult: Decimal
    risk_pct: Decimal
    f_star_estimate: Decimal

    def __post_init__(self) -> None:
        """Validate all inputs are strictly positive.

        Raises:
            ValueError: On any non-positive input field.
        """
        for name, val in [
            ("equity", self.equity),
            ("entry_px", self.entry_px),
            ("atr", self.atr),
            ("stop_atr_mult", self.stop_atr_mult),
            ("risk_pct", self.risk_pct),
            ("f_star_estimate", self.f_star_estimate),
        ]:
            if val <= Decimal("0"):
                raise ValueError(f"SizingInput.{name} must be > 0, got {val!r}")


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Immutable result of the T-1 sizing computation.

    Args:
        qty: Computed base-asset quantity to trade. Always > 0 when the
            computation succeeds.
        notional: Quote-currency notional value = ``qty * entry_px``.
        atr_notional: The ATR-baseline notional before capping.
        kelly_notional: The Kelly-cap notional before capping.
        hard_cap_notional: The 5%-of-equity hard cap.
        binding_cap: Human-readable label of which cap was binding
            (``"atr"``, ``"kelly"``, or ``"hard_cap"``).

    Reference: spec-3-order-router change.md D-2.
    """

    qty: Decimal
    notional: Decimal
    atr_notional: Decimal
    kelly_notional: Decimal
    hard_cap_notional: Decimal
    binding_cap: str


class SizingCalculator:
    """Stateless T-1 position-sizing calculator for Swing MVP1.

    Instantiate once; call :meth:`compute` for each sizing request.
    Holds no mutable state — all inputs arrive at call time.

    Reference: pre-registration-v1.md §5.4; spec-3-order-router change.md D-2.
    """

    def compute(self, inp: SizingInput) -> Result[SizingResult, DomainError]:
        """Compute the T-1 position size using the three-cap min() formula.

        Formula (pre-reg §5.4 binding):

        .. code-block:: text

            atr_notional   = (equity * risk_pct) / stop_atr_mult
            kelly_notional = equity * min(0.25 * f_star_estimate, 0.05)
            hard_cap       = 0.05 * equity
            notional       = min(atr_notional, kelly_notional, hard_cap)
            qty            = notional / entry_px

        Args:
            inp: Immutable sizing input bundle.

        Returns:
            ``Success(SizingResult)`` with the computed position size, or
            ``Failure(InvalidLot)`` when the computed qty would be zero or
            negative (degenerate inputs that pass ``__post_init__`` but
            yield nonsensical output — e.g. ``stop_atr_mult`` so large
            that ``atr_notional`` underflows to zero in Decimal precision).

        Reference: pre-registration-v1.md §5.4, SA-7, SA-1.
        """
        atr_notional: Decimal = (inp.equity * inp.risk_pct) / inp.stop_atr_mult

        # Kelly cap: 0.25 * f_star_estimate, floored at 5% of equity.
        kelly_fraction = min(
            _KELLY_FRACTION_MULTIPLIER * inp.f_star_estimate,
            _KELLY_FLOOR_FRACTION,
        )
        kelly_notional: Decimal = inp.equity * kelly_fraction

        hard_cap_notional: Decimal = _HARD_CAP_FRACTION * inp.equity

        notional = min(atr_notional, kelly_notional, hard_cap_notional)

        if notional <= Decimal("0"):
            return Failure(
                InvalidLot(
                    reason=(
                        f"SizingCalculator: computed notional is zero or negative "
                        f"(atr={atr_notional!r}, kelly={kelly_notional!r}, "
                        f"hard_cap={hard_cap_notional!r}). "
                        f"Check sizing inputs for degenerate values."
                    )
                )
            )

        qty = notional / inp.entry_px

        if qty <= Decimal("0"):
            return Failure(
                InvalidLot(
                    reason=(
                        f"SizingCalculator: computed qty is zero or negative "
                        f"(notional={notional!r}, entry_px={inp.entry_px!r})."
                    )
                )
            )

        # Identify which cap was binding (first match when equal).
        if notional == atr_notional:
            binding_cap = "atr"
        elif notional == kelly_notional:
            binding_cap = "kelly"
        else:
            binding_cap = "hard_cap"

        return Success(
            SizingResult(
                qty=qty,
                notional=notional,
                atr_notional=atr_notional,
                kelly_notional=kelly_notional,
                hard_cap_notional=hard_cap_notional,
                binding_cap=binding_cap,
            )
        )
