"""Contract test: Telegram and Slack still answer in the shape the fakes are held to.

The notification fakes (``tests/support/fake_notify/``, issue #972) answer with
payloads that pass :mod:`tests.support.fake_notify.schema`. This file runs the
same checks on what the live vendors answer to the read-only probes, so a
format change fails the daily contract run instead of leaving the fakes
quietly out of date.

Read-only calls (``getMe``, ``auth.test``) that send nothing to any chat.
Each test skips when its token is unset. Shapes only: the checks name the
field that broke, never a token.

Excluded from the default run by ``-m 'not contract'``; run with
``uv run pytest -m contract tests/contract/test_notify_fakes_contract.py``.
"""

import os

import httpx
import pytest

from tests.support.fake_notify import schema

_TIMEOUT_SECONDS = 10.0


def _token(name: str) -> str:
    token = os.environ.get(name, "").strip()
    if not token:
        pytest.skip(f"{name} not set; skip the live contract check")
    return token


@pytest.mark.contract
def test_live_telegram_get_me_meets_the_fake_contract() -> None:
    """The live ``getMe`` answer passes the checks every fake answer must pass."""
    token = _token("HERMES_TELEGRAM__BOT_TOKEN")
    response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=_TIMEOUT_SECONDS)

    assert schema.telegram_response_problems(response.json()) == []


@pytest.mark.contract
def test_live_slack_auth_test_meets_the_fake_contract() -> None:
    """The live ``auth.test`` answer passes the checks every fake answer must pass."""
    token = _token("HERMES_SLACK__BOT_TOKEN")
    response = httpx.post(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT_SECONDS,
    )

    assert schema.slack_response_problems(response.json()) == []
