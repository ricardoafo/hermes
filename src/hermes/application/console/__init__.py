"""Operator console: read-model contract, view objects, visibility and renderers.

Every console surface (CLI, TUI, pulled Telegram replies) reads through
:class:`ConsoleReadModel` and renders through :mod:`hermes.application.console.render`.

Reference: openspec/changes/console-read-model/change.md.
"""

from hermes.application.console.read_model import ConsoleReadModel, ViewNotAvailableError
from hermes.application.console.views import (
    QUIET_ACTIVITY_KINDS,
    ActivityDay,
    ActivityItem,
    ActivityKind,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
    ShadowTestStatus,
    Tax,
    TaxBand,
    TaxEstimate,
    TaxPending,
    TimelineEvent,
    WatchedPair,
    WaterfallColumn,
    tax_amount,
)
from hermes.application.console.visibility import OPERATOR_SURFACES, Surface, pnl_visible

__all__ = [
    "OPERATOR_SURFACES",
    "QUIET_ACTIVITY_KINDS",
    "ActivityDay",
    "ActivityItem",
    "ActivityKind",
    "ConsoleReadModel",
    "Heartbeat",
    "HeartbeatState",
    "PortfolioRange",
    "PortfolioSummary",
    "PositionDetail",
    "PositionRow",
    "ShadowTest",
    "ShadowTestStatus",
    "Surface",
    "Tax",
    "TaxBand",
    "TaxEstimate",
    "TaxPending",
    "TimelineEvent",
    "ViewNotAvailableError",
    "WatchedPair",
    "WaterfallColumn",
    "pnl_visible",
    "tax_amount",
]
