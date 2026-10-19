"""Sentry error-reporting initialization.

Sentry differs from the notifier adapters — it's a SDK we set up once
at process start. After init, unhandled exceptions and explicit
`capture_exception()` calls are shipped to Sentry with the tagged
environment (dev/sandbox/prod).

Design notes
------------

- Init is idempotent — safe to call multiple times (SDK deduplicates).
- If DSN is empty, init is a no-op — silently disabled. This matches
  the "empty ping_url = disabled" pattern from Healthchecks.
- Environment tag mirrors :attr:`HermesConfig.env` (dev / sandbox / prod)
  so Sentry's UI can filter accordingly.
- No PII in tags — we tag the runtime env, not user info
  (``send_default_pii`` stays unset: the SDK then sends no PII, and passing
  it is deprecated since sentry-sdk 2.72).
- No stack-frame local variables on events (``include_local_variables=False``).
  The SDK's scrubber masks keys named like ``token`` or ``secret``, but a
  credential inside another local (the Telegram adapter builds
  ``url = f"/bot{token}/..."``) would ship to Sentry as-is.
- Traces sample rate = 0 (no performance monitoring in MVP1 — the free
  tier is error-only anyway).
- Framework integrations are DISABLED (empty ``integrations=[]``,
  ``default_integrations=False``). Hermes is vanilla Python — no
  Django/Flask/FastAPI/etc. Enabling framework auto-detection would
  pull in code that scans for modules we do not use and adds risk
  without value.
- Failure modes: any exception raised BY the SDK itself (init failure,
  serialization glitch during capture) is caught + logged via structlog.
  A crashing error-reporter would be worse than one that silently drops
  the report — the caller must never take on Sentry's failure.

Layering / imports
------------------

This module lives under ``infrastructure/`` — it MAY import ``config``,
``domain.errors``, and ``structlog``. It MUST NOT be imported by
``domain/`` or ``application/`` (import-linter contracts
``domain-is-pure`` + ``application-imports-only-domain-and-ports``
enforce that). CLI wiring (``entrypoints/cli.py``) is the only expected
call site for :func:`initialize`; the router / adapters can call
:func:`capture` inline for exceptions they catch-and-continue on.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
import structlog

__all__ = ["capture", "initialize", "set_correlation_id"]

_log = structlog.get_logger(__name__)

# Tag key mirroring the router's correlation-ID convention. Kept as a
# module-level constant so tests can assert on the exact tag without
# hard-coding the literal string in multiple places.
_CORRELATION_TAG: str = "correlation_id"
# Scope-context key used by :func:`capture` when ``extra`` is provided.
# Distinct from "extra" (Sentry's own catch-all) so operators can grep
# hermes-specific context in the Sentry UI.
_STRUCTURED_CONTEXT_KEY: str = "structured_context"


def initialize(dsn: str, environment: str) -> None:
    """Initialize the Sentry SDK with hermes-safe defaults.

    Called ONCE from the CLI startup after the config loads. Safe to
    call multiple times — the SDK deduplicates. An empty ``dsn`` is a
    no-op (integration DISABLED, matches
    :class:`HealthchecksConfig.enabled`).

    Args:
        dsn: The Sentry Data Source Name. Empty string disables the
            integration silently — no wire calls happen.
        environment: Environment tag attached to every event
            (``"dev"`` / ``"sandbox"`` / ``"qa"`` / ``"staging"`` / ``"prod"``). Mirrors
            :attr:`HermesConfig.env` so Sentry's UI can filter accordingly.

    Notes:
        This function never raises — any SDK-init failure is caught and
        logged via structlog. A crashing error-reporter would be worse
        than one that silently drops events.
    """
    if not dsn:
        # Empty DSN = integration disabled. Silent no-op — matches the
        # "empty ping_url = disabled" pattern from Healthchecks. Do NOT
        # log at info level here; a fresh clone would spam this line on
        # every CLI invocation.
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            # No PII: we do not want user emails / IP / session tokens
            # attached to events (LOPDGDD / GDPR posture).
            # Locals can hold credentials under names the scrubber does
            # not know (see the module docstring).
            include_local_variables=False,
            # No performance / trace sampling in MVP1. The free tier is
            # error-only; enabling traces would burn quota without value.
            traces_sample_rate=0.0,
            # No framework integrations — hermes is vanilla Python. Auto-
            # discovering Django/Flask/etc. would scan for modules we do
            # not use.
            default_integrations=False,
            integrations=[],
        )
    except Exception as exc:
        # Broad except deliberate: any failure inside sentry_sdk.init
        # (bad DSN parse, network probe, dependency version mismatch)
        # MUST NOT crash the CLI. Log + continue — Sentry is dead, hermes
        # keeps running.
        _log.warning(
            "sentry.init.failed",
            error=str(exc),
            error_type=type(exc).__name__,
            environment=environment,
        )


def capture(error: Exception, *, extra: dict[str, Any] | None = None) -> None:
    """Ship an exception to Sentry with optional structured context.

    Thin wrapper around :func:`sentry_sdk.capture_exception`. Use this
    for exceptions we DO catch-and-continue but still want visibility
    on in Sentry — e.g. an adapter error the application layer decides
    to swallow. Unhandled exceptions are already captured by Sentry's
    global ``sys.excepthook``; do NOT wrap every function call in
    try/except-capture — that is an anti-pattern.

    When ``extra`` is provided, its contents are attached under the
    ``structured_context`` scope key (distinct from Sentry's built-in
    ``extra`` catch-all) so operators can filter hermes-specific
    context in the Sentry UI.

    Args:
        error: The caught exception to ship. Sentry captures the full
            traceback via the frame at the call site.
        extra: Optional structured context dict — anything the caller
            wants to attach (adapter name, correlation ID, retry
            counter, etc.). Values must be JSON-serializable; Sentry
            handles the serialization internally.

    Notes:
        This function never raises. Any error inside the Sentry SDK
        during capture (serialization glitch, transport hiccup) is
        caught and logged via structlog. A crashing capture would
        propagate an infra failure into whatever code path was
        already handling a domain error.
    """
    try:
        if extra:
            # new_scope() forks the current scope for this one capture, so
            # the context does not leak into the next event.
            with sentry_sdk.new_scope() as scope:
                scope.set_context(_STRUCTURED_CONTEXT_KEY, dict(extra))
                sentry_sdk.capture_exception(error)
        else:
            sentry_sdk.capture_exception(error)
    except Exception as sdk_exc:
        # See the initialize() rationale: a crashing error-reporter is
        # worse than one that silently drops.
        _log.warning(
            "sentry.capture.failed",
            error=str(sdk_exc),
            error_type=type(sdk_exc).__name__,
            original_error=type(error).__name__,
        )


def set_correlation_id(correlation_id: str) -> None:
    """Tag the current Sentry scope with a correlation ID.

    Every event captured after this call (until the scope is popped or
    a new correlation ID overrides) carries the tag
    ``correlation_id=<value>``. Matches the router's correlation-ID
    convention (see :mod:`hermes.infrastructure.logging` —
    :func:`bind_correlation_id`) so a single workflow tick's log lines
    AND its Sentry events share the same trace anchor.

    Uses :func:`sentry_sdk.set_tag` (not ``set_context``) because tags
    are indexed + searchable in the Sentry UI — an operator can pivot
    from a Slack alert (which carries the correlation-tail) directly to
    the matching Sentry event.

    Args:
        correlation_id: The correlation ID string. Passed verbatim to
            :func:`sentry_sdk.set_tag`.

    Notes:
        This function never raises — any SDK exception is caught + logged.
        If Sentry is disabled (empty DSN), :func:`sentry_sdk.set_tag` is
        itself a no-op (the SDK's Hub is a NoOpHub before init) — no
        guard needed here.
    """
    try:
        sentry_sdk.set_tag(_CORRELATION_TAG, correlation_id)
    except Exception as sdk_exc:
        _log.warning(
            "sentry.set_correlation_id.failed",
            error=str(sdk_exc),
            error_type=type(sdk_exc).__name__,
        )
