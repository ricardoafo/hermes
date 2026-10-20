"""Trading pair value object — base/quote currency combination.

``Pair`` represents a market like BTC/USDT. Base and quote must differ.
Parsing supports both ``"BTC/USDT"`` (slash-separated) and ``"BTCUSDT"``
(concatenated) formats via a heuristic that tries known currency prefixes.

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from dataclasses import dataclass

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency

__all__ = ["Pair"]


@dataclass(frozen=True, slots=True)
class Pair:
    """Immutable trading pair: a base currency and a quote currency.

    The canonical string form is ``"BASE/QUOTE"`` (e.g. ``"BTC/USDT"``).

    Reference: code-conventions.md §1.
    """

    base: Currency
    quote: Currency

    def __post_init__(self) -> None:
        """Validate that base and quote are distinct."""
        if self.base == self.quote:
            raise ValueError(f"Pair base and quote must differ, got {self.base!r} twice")

    @classmethod
    def from_str(cls, s: str) -> Result[Pair, DomainError]:
        """Parse a trading pair string.

        Accepts both slash-separated (``"BTC/USDT"``) and concatenated
        (``"BTCUSDT"``) forms. The heuristic for concatenated form tries
        every known currency as a prefix, longest-first.

        Args:
            s: Pair string in slash or concatenated form.

        Returns:
            ``Success(Pair)`` on valid input, ``Failure(InvalidLot)`` otherwise.

        Reference: code-conventions.md §4.
        """
        s = s.strip().upper()
        if "/" in s:
            base_str, quote_str = s.split("/", 1)
        else:
            # Heuristic split: try each known currency as a prefix (longest first to
            # avoid "BTC" matching "BTCUSDT" when "BTCUSDT" has no quote remainder).
            currencies_by_len = sorted(Currency, key=lambda c: len(c.value), reverse=True)
            base_str = ""
            quote_str = ""
            for candidate in currencies_by_len:
                if s.startswith(candidate.value):
                    remainder = s[len(candidate.value) :]
                    if remainder in Currency.__members__:
                        base_str = candidate.value
                        quote_str = remainder
                        break
            if not base_str:
                return Failure(
                    InvalidLot(reason=f"Cannot split concatenated pair {s!r} into known currencies")
                )

        base_result = Currency.from_str(base_str)
        if isinstance(base_result, Failure):
            return Failure(InvalidLot(reason=f"Unknown base currency {base_str!r} in pair {s!r}"))
        quote_result = Currency.from_str(quote_str)
        if isinstance(quote_result, Failure):
            return Failure(InvalidLot(reason=f"Unknown quote currency {quote_str!r} in pair {s!r}"))

        base = base_result.unwrap()
        quote = quote_result.unwrap()
        if base == quote:
            return Failure(
                InvalidLot(reason=f"Pair base and quote must differ, got {base!r} twice")
            )
        return Success(cls(base=base, quote=quote))

    def __str__(self) -> str:
        """Return ``"BASE/QUOTE"`` canonical form."""
        return f"{self.base}/{self.quote}"
