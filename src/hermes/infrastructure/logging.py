"""Structured logging config.

Wires structlog for hermes: JSON output to stdout (journald captures) in
prod, ConsoleRenderer in dev. Every log line carries a correlation_id
bound via contextvars — set once per rebalance cycle / user command /
adapter call, propagates through every nested call in the same task.

## Field starter set

Every log line has:

- ``timestamp`` — ISO 8601 UTC with Z suffix
- ``level`` — lowercase (``info``, ``warning``, ``error``, ...)
- ``event`` — dotted namespace string (e.g. ``rebalance.cycle.decided``);
  NOT free-text — grepping journald depends on this
- ``correlation_id`` — full ULID (26 chars, Crockford base32); last 6
  chars go into Telegram tails per ADR-008 § Correlation ID convention
- ``component`` — module path (e.g. ``hermes.application.rebalance``);
  source of the log call (typically from ``structlog.get_logger(__name__)``)

Additional fields per feature via ``bind_contextvars()``. Rules:

- Money values as strings (Decimal-serialized), never floats
- Never log PII (LOPDGDD — see ADR-008 § What NEVER gets logged)
- Prefer dotted-namespace event names over free-text messages

## Usage

    from hermes.infrastructure.logging import (
        configure_logging, bind_correlation_id, new_correlation_id,
    )
    import structlog

    configure_logging(env="prod")  # or "dev" for local
    log = structlog.get_logger(__name__)

    bind_correlation_id(new_correlation_id())
    log.info("rebalance.cycle.started", pair="BTCUSDT")
"""

from __future__ import annotations

import logging

import structlog
import ulid
from structlog.contextvars import bind_contextvars, merge_contextvars

_INFO_LEVEL: int = logging.INFO
_JSON_ENVS: frozenset[str] = frozenset({"prod", "staging"})


def configure_logging(*, env: str = "prod") -> None:
    """Configure structlog processors for the given environment.

    Args:
        env: ``"prod"`` and ``"staging"`` use ``JSONRenderer`` (journald-friendly,
            per ADR-008 §1); staging logs like prod so its journal reads the same.
            ``"dev"`` uses ``ConsoleRenderer`` with colors (human-readable local).

    Idempotent — safe to call multiple times.
    """
    common_processors: list[structlog.types.Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if env in _JSON_ENVS
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[*common_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(_INFO_LEVEL),
        context_class=dict,
        cache_logger_on_first_use=True,
    )


def new_correlation_id() -> str:
    """Generate a fresh ULID (26-char Crockford base32).

    ULIDs sort lexicographically by creation time — useful when grepping
    journald for events in temporal order.
    """
    return str(ulid.new())


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation ID to the current contextvar scope.

    All subsequent log calls in the same async task will include
    ``correlation_id=<value>`` automatically via
    ``structlog.contextvars.merge_contextvars``.
    """
    bind_contextvars(correlation_id=correlation_id)
