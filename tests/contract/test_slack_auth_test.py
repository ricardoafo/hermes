"""Contract test — real ``auth.test`` call against the Slack Web API.

Mirrors the pattern established by ``test_telegram_getme.py`` (PR B):
a READ-ONLY probe that hits the real third-party sandbox to detect
upstream drift before it silently breaks the wire path our unit mocks
assume.

Contract vs unit
----------------

- **Unit** (``tests/unit/``, mocked ``respx``): asserts our code's
  behaviour against a fake wire. Fast, hermetic, per-PR CI.
- **Contract** (``tests/contract/``, real HTTPS): asserts the third
  party STILL responds the way our unit mock assumes it does.
  Skips gracefully when credentials are absent. Runs nightly (or on
  ``pytest -m contract`` locally); catches upstream API drift before
  it silently breaks the wire path.

``auth.test`` is Slack's canonical READ-ONLY validation endpoint — it
confirms the bot token is valid AND returns the workspace name +
bot user WITHOUT posting anything to any channel. Safe to run in any
tier, safe to loop in a watchdog script.

Reference: docs/decisions/2026-08-23-contract-test-tier.md;
https://api.slack.com/methods/auth.test.
"""

import os

import httpx
import pytest

from hermes.adapters.notifier.slack_adapter import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)


@pytest.mark.contract
def test_auth_test_against_real_slack_api() -> None:
    """Real ``auth.test`` returns ok=true with team + user — asserts upstream shape.

    Skips when ``HERMES_SLACK__BOT_TOKEN`` is unset (fresh clone, CI
    without secrets, developer running the unit path only).
    ``DEFAULT_CHANNEL`` is not required for ``auth.test`` — it's a
    token-only, read-only probe — but :meth:`SlackConfig.from_env`
    requires both, so we inject a placeholder for the channel.
    """
    token = os.environ.get("HERMES_SLACK__BOT_TOKEN", "").strip()
    if not token:
        pytest.skip(
            "HERMES_SLACK__BOT_TOKEN not set — skip contract test "
            "(populate .env.local or CI secret to run this tier)"
        )

    # DEFAULT_CHANNEL is not needed for auth.test but from_env requires
    # it — inject a placeholder so config validates without leaking a
    # real channel.
    config = SlackConfig(bot_token=token, default_channel="#contract-test-placeholder")
    adapter = SlackAdapter.create(config)
    try:
        try:
            result = adapter.auth_test()
        except SlackNotifierError as exc:
            pytest.fail(
                f"Real Slack auth.test failed: {exc}. "
                f"If the token is a placeholder, unset HERMES_SLACK__BOT_TOKEN "
                f"to skip this test instead."
            )
        # Real response shape (per https://api.slack.com/methods/auth.test):
        # ok (bool True), team (str), user (str), team_id (str), user_id (str), url (str).
        # Assert the two fields our health probe reads — drift on either
        # would break the entire `hermes health slack` path.
        assert result.get("ok") is True, f"Slack auth.test returned ok != True: {result}"
        assert isinstance(result.get("team"), str), (
            f"Slack auth.test missing 'team' string: {result}"
        )
        assert isinstance(result.get("user"), str), (
            f"Slack auth.test missing 'user' string: {result}"
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"Transport failure calling Slack auth.test: {exc!r}")
    finally:
        adapter.close()
