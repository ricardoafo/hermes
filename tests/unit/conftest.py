"""Unit-tier fixtures.

Every unit test runs with ``decimal.FloatOperation`` trapped (issue #842).
Money, prices and quantities are ``Decimal``; a ``float`` mixed into that
maths (``Decimal(0.1)``, ``Decimal("1") < 0.5``) silently carries binary
rounding error into a fill, a fee or a tax figure. With the trap set, the
mix raises ``decimal.FloatOperation`` and the test fails at the line that
did it.
"""

import decimal
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _trap_float_operation() -> Iterator[None]:
    """Run the test in a decimal context where a float in Decimal maths raises."""
    with decimal.localcontext() as context:
        context.traps[decimal.FloatOperation] = True
        yield
