"""Pin the signed risk-overlay parameters once, against the pre-registration text.

Invariant: each risk rule's threshold equals the value signed in pre-registration
§5.5 (RO-01 to RO-07) and §10 (RO-08). The behaviour tests derive their boundaries
from these constants, so this table is the only place a silent edit is caught.
"""

from decimal import Decimal

import pytest

from hermes.domain.risk import (
    correlation_lock,
    depeg_halt,
    depeg_warn,
    macro_atr_halt,
    max_slippage,
    portfolio_drawdown,
    spread_spike,
    stale_book,
)

_SIGNED: list[tuple[str, object, str, object]] = [
    ("RO-01", correlation_lock, "_CORRELATION_THRESHOLD", 0.80),
    ("RO-02", spread_spike, "_SPREAD_MULTIPLIER", Decimal("10")),
    ("RO-03 single", max_slippage, "_SINGLE_SLIPPAGE_LIMIT", Decimal("0.005")),
    ("RO-03 cumulative", max_slippage, "_CUMULATIVE_SLIPPAGE_LIMIT", Decimal("0.02")),
    ("RO-04", stale_book, "_MAX_ORDERBOOK_AGE_SECONDS", 2.0),
    ("RO-05", depeg_warn, "_WARN_THRESHOLD", Decimal("0.015")),
    ("RO-06", depeg_halt, "_HALT_THRESHOLD", Decimal("0.030")),
    ("RO-07", macro_atr_halt, "_BTC_LOG_RETURN_LIMIT", -0.10),
    ("RO-08", portfolio_drawdown, "_DRAWDOWN_HALT_THRESHOLD", Decimal("0.15")),
]


@pytest.mark.parametrize(("rule", "module", "name", "signed"), _SIGNED, ids=[r[0] for r in _SIGNED])
def test_threshold_equals_the_signed_value(
    rule: str, module: object, name: str, signed: object
) -> None:
    """Pre-reg §5.5 / §10: a risk threshold changes only through a new pre-registration."""
    assert getattr(module, name) == signed, f"{rule} no longer matches the signed value"
