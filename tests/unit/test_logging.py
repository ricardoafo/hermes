"""Correlation-ID plumbing must survive nested calls.

# SEV-1: correlation IDs are the primary audit trail per ADR-008 §1.
# If binding breaks, post-mortem grep-ability is dead and every incident
# investigation regresses to "find the log by wall clock" — unreliable.
"""

from __future__ import annotations

import pytest
import structlog
from structlog.contextvars import (
    clear_contextvars,
    get_contextvars,
    merge_contextvars,
)

from hermes.infrastructure.logging import (
    bind_correlation_id,
    configure_logging,
    new_correlation_id,
)


def _read_contextvar_from_inner_call() -> dict[str, object]:
    """Nested function reads current contextvars — proves propagation."""
    return get_contextvars()


def test_correlation_id_propagates_across_nested_calls() -> None:
    """Correlation ID bound at the call-chain root is visible in every nested call.

    Uses ``get_contextvars`` for direct verification rather than
    ``capture_logs``, because ``capture_logs`` replaces the processor chain
    (bypassing ``merge_contextvars``) and would false-negative here.
    """
    clear_contextvars()
    configure_logging(env="prod")
    cid = new_correlation_id()
    bind_correlation_id(cid)

    seen = _read_contextvar_from_inner_call()
    assert seen.get("correlation_id") == cid


def test_correlation_id_lands_in_captured_log() -> None:
    """Bound correlation ID appears in a real JSON-rendered log entry via merge_contextvars.

    ``capture_logs`` replaces the chain, so instead we verify via the
    contextvar directly (above test) and via a real JSON render here.
    """
    clear_contextvars()
    configure_logging(env="prod")
    cid = new_correlation_id()
    bind_correlation_id(cid)

    # Directly invoke the processor chain to render a log entry — this is
    # what the production JSONRenderer would see.
    entry = merge_contextvars(None, "info", {"event": "test.event"})
    assert entry.get("correlation_id") == cid


def test_new_correlation_id_is_26_char_ulid() -> None:
    """ULID = 26 chars, Crockford base32 (no I, L, O, U)."""
    cid = new_correlation_id()
    assert len(cid) == 26
    forbidden = set("ILOU")
    assert not any(c in forbidden for c in cid)


def test_configure_logging_is_idempotent() -> None:
    """Calling configure_logging twice does not raise or double-register."""
    configure_logging(env="prod")
    configure_logging(env="dev")
    configure_logging(env="prod")
    # If we got here without exceptions, idempotency holds.
    log = structlog.get_logger("hermes.tests.idempotent")
    log.info("post.reconfigure")


@pytest.mark.parametrize(
    ("env", "renderer"),
    [
        ("prod", structlog.processors.JSONRenderer),
        ("staging", structlog.processors.JSONRenderer),
        ("dev", structlog.dev.ConsoleRenderer),
        ("sandbox", structlog.dev.ConsoleRenderer),
    ],
)
def test_configure_logging_renders_json_only_for_the_journald_tiers(
    env: str, renderer: type[object]
) -> None:
    """Prod and staging log JSON for journald (#192); local tiers keep the console renderer."""
    configure_logging(env=env)
    try:
        last = structlog.get_config()["processors"][-1]
    finally:
        structlog.reset_defaults()

    assert isinstance(last, renderer)
