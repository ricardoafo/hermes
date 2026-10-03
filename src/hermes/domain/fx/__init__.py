"""FX rate domain — official-source foreign-exchange conversion primitives.

Public re-exports for :class:`~hermes.domain.fx.fx_rate.FxRate` and the
EUR pricing of trades (:mod:`hermes.domain.fx.eur_pricing`).

Domain purity: this package imports only other domain value objects and
stdlib. No pydantic, no httpx, no asyncio (enforced by ``import-linter``
contracts ``domain-no-pydantic`` and ``domain-no-async-or-network``).

Reference: docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
"""

from __future__ import annotations

from hermes.domain.fx.eur_pricing import EurPricing, EurStatus
from hermes.domain.fx.fx_rate import FxRate

__all__ = ["EurPricing", "EurStatus", "FxRate"]
