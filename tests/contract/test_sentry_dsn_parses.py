"""Contract test — real DSN parses via :class:`sentry_sdk.utils.Dsn`.

Sentry does not offer a hermes-safe "ping" endpoint: any wire call ships
a real event that eats the operator's free-tier quota (5,000 events/month).
Instead the contract test validates the DSN URL string parses correctly —
which is exactly what :func:`sentry_sdk.init` does before opening the
transport. A parse failure here catches the same class of misconfig the
SDK would raise at init time.

No network is opened by this test.

Reference: docs/decisions/2026-08-23-contract-test-tier.md;
https://docs.sentry.io/product/sentry-basics/dsn-explainer/.
"""

import os

import pytest


@pytest.mark.contract
def test_real_dsn_parses_via_sentry_sdk() -> None:
    """Real ``HERMES_SENTRY__DSN`` parses without :class:`BadDsn`.

    Skips when ``HERMES_SENTRY__DSN`` is unset (fresh clone, CI without
    secrets, developer running the unit path only). Does NOT initialize
    the SDK and does NOT send any event — only the URL parse is
    exercised. That mirrors what ``hermes health sentry`` does in
    production and validates our health check's assumption stays true
    against the real DSN shape.
    """
    dsn = os.environ.get("HERMES_SENTRY__DSN", "").strip()
    if not dsn:
        pytest.skip(
            "HERMES_SENTRY__DSN not set — skip contract test "
            "(populate .env.local or CI secret to run this tier)"
        )

    from sentry_sdk.utils import BadDsn, Dsn  # noqa: PLC0415 — lazy import after skip check

    try:
        parsed = Dsn(dsn)
    except BadDsn as exc:
        pytest.fail(
            f"Real Sentry DSN failed to parse: {exc}. "
            f"If HERMES_SENTRY__DSN is a placeholder value (e.g. from .env.local.example), "
            f"unset it to skip this test instead."
        )

    assert parsed.host, "parsed DSN missing host"
    assert parsed.project_id, "parsed DSN missing project_id"
