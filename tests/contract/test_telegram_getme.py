"""Contract test — real ``getMe`` call against the Telegram Bot API.

This is the FIRST contract-tier test in the repo; it establishes the
pattern for PRs D/E/G to follow (Slack ``auth.test``, Healthchecks
``/ping/…/log``, Sentry ``client_report``).

Contract vs unit
----------------

- **Unit** (``tests/unit/``, mocked ``respx``): asserts our code's
  behaviour against a fake wire. Fast, hermetic, per-PR CI.
- **Contract** (``tests/contract/``, real HTTPS): asserts the third
  party STILL responds the way our unit mock assumes it does.
  Skips gracefully when credentials are absent. Runs nightly (or on
  ``pytest -m contract`` locally); catches upstream API drift before
  it silently breaks the wire path.

Reference: docs/decisions/2026-08-23-contract-test-tier.md.
"""

import os

import httpx
import pytest

from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)


@pytest.mark.contract
def test_get_me_against_real_telegram_api() -> None:
    """Real ``getMe`` returns ok=true with a bot object — asserts upstream shape.

    Skips when ``HERMES_TELEGRAM__BOT_TOKEN`` is unset (fresh clone,
    CI without secrets, developer running the unit path only). CHAT_ID
    is not required for ``getMe`` — it's a token-only, read-only probe.
    """
    token = os.environ.get("HERMES_TELEGRAM__BOT_TOKEN", "").strip()
    if not token:
        pytest.skip(
            "HERMES_TELEGRAM__BOT_TOKEN not set — skip contract test "
            "(populate .env.local or CI secret to run this tier)"
        )

    # CHAT_ID is not needed for getMe but from_env requires it — inject a
    # placeholder so the config validates without leaking a real chat.
    config = TelegramConfig(bot_token=token, chat_id="contract-test-placeholder")
    adapter = TelegramAdapter.create(config)
    try:
        try:
            result = adapter.get_me()
        except TelegramNotifierError as exc:
            pytest.fail(
                f"Real Telegram getMe failed: {exc}. "
                f"If the token is a placeholder, unset HERMES_TELEGRAM__BOT_TOKEN "
                f"to skip this test instead."
            )
        # Real response shape (per https://core.telegram.org/bots/api#getme):
        # id (int), is_bot (bool True), first_name (str), username (str).
        # Assert the two fields our health probe reads — drift on either
        # would break the entire `hermes health telegram` path.
        assert result.get("is_bot") is True, f"Telegram getMe returned is_bot != True: {result}"
        assert isinstance(result.get("username"), str), (
            f"Telegram getMe missing 'username' string: {result}"
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"Transport failure calling Telegram getMe: {exc!r}")
    finally:
        adapter.close()
