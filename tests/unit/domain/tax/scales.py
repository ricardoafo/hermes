"""The 2025/2026 IRPF scales as config/tax_scales.yaml states them, for tests."""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.tax.scale import SavingsBand, SavingsScale

SCALE_2026 = SavingsScale(
    bands=(
        SavingsBand(up_to=Decimal("6000"), rate=Decimal("0.19")),
        SavingsBand(up_to=Decimal("50000"), rate=Decimal("0.21")),
        SavingsBand(up_to=Decimal("200000"), rate=Decimal("0.23")),
        SavingsBand(up_to=Decimal("300000"), rate=Decimal("0.27")),
        SavingsBand(up_to=None, rate=Decimal("0.30")),
    )
)


def _scale(*bands: tuple[str | None, str]) -> SavingsScale:
    return SavingsScale(
        bands=tuple(
            SavingsBand(up_to=None if limit is None else Decimal(limit), rate=Decimal(rate))
            for limit, rate in bands
        )
    )


# Ley 35/2006 art. 63.1.1 (state general scale, 2025 and 2026).
STATE_GENERAL_2026 = _scale(
    ("12450", "0.095"),
    ("20200", "0.12"),
    ("35200", "0.15"),
    ("60000", "0.185"),
    ("300000", "0.225"),
    (None, "0.245"),
)

# Decreto Legislativo 1/2010 de la Region de Murcia art. 2 (2025 and 2026).
MURCIA_GENERAL_2026 = _scale(
    ("12450", "0.095"),
    ("20200", "0.112"),
    ("34000", "0.133"),
    ("60000", "0.179"),
    (None, "0.225"),
)
