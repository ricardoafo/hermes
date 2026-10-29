"""Contract test — real ``/start`` ping against Healthchecks.io.

Mirrors the pattern established by ``test_telegram_getme.py`` (PR B)
and ``test_slack_auth_test.py`` (PR D): a probe against the real
third-party endpoint that detects upstream drift before it silently
breaks the wire path our unit mocks assume.

Contract vs unit
----------------

- **Unit** (``tests/unit/``, mocked ``respx``): asserts our code's
  behaviour against a fake wire. Fast, hermetic, per-PR CI.
- **Contract** (``tests/contract/``, real HTTPS): asserts the third
  party STILL responds the way our unit mock assumes it does.
  Skips gracefully when credentials are absent. Runs nightly (or on
  ``pytest -m contract`` locally); catches upstream API drift before
  it silently breaks the wire path.

``ping_start`` is the safe endpoint for a contract test — it is
idempotent + informational, and does NOT fire the dead-man's-switch
alert. Calling ``ping_fail`` from a test would spam the operator's
alert channels every CI run (documented anti-pattern in the adapter
docstring).

Reference: docs/decisions/2026-08-23-contract-test-tier.md;
https://healthchecks.io/docs/http_api/.
"""

import os

import httpx
import pytest

from hermes.adapters.healthchecks import (
    HealthchecksAdapter,
    HealthchecksAdapterConfig,
    HealthchecksError,
)


@pytest.mark.contract
def test_ping_start_against_real_healthchecks_io() -> None:
    """Real ``/start`` ping returns 2xx — asserts upstream reachability + URL shape.

    Skips when ``HERMES_HEALTHCHECKS__PING_URL`` is unset (fresh clone,
    CI without secrets, developer running the unit path only).
    Fires ``ping_start`` (informational — does NOT trigger the
    dead-man's-switch alert) against the real ping URL and asserts
    the adapter did not raise. Healthchecks returns ``"OK"`` in the
    response body on success; the assertion here is that the adapter
    accepts that response without raising :class:`HealthchecksError`.
    """
    url = os.environ.get("HERMES_HEALTHCHECKS__PING_URL", "").strip()
    if not url:
        pytest.skip(
            "HERMES_HEALTHCHECKS__PING_URL not set — skip contract test "
            "(populate .env.local or CI secret to run this tier)"
        )

    config = HealthchecksAdapterConfig(ping_url=url)
    adapter = HealthchecksAdapter.create(config)
    try:
        try:
            adapter.ping_start(details="hermes contract test")
        except HealthchecksError as exc:
            pytest.fail(
                f"Real Healthchecks ping_start failed: {exc}. "
                f"If the URL is a placeholder, unset HERMES_HEALTHCHECKS__PING_URL "
                f"to skip this test instead."
            )
    except httpx.HTTPError as exc:
        pytest.fail(f"Transport failure calling Healthchecks ping_start: {exc!r}")
    finally:
        adapter.close()
