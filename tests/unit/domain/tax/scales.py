"""The 2025/2026 savings scale as config/tax_scales.yaml states it, for tests."""

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
