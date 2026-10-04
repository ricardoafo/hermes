"""Tests for ``hermes preview`` under the v2 templates, and the migration test.

Invariants protected here:

- every event, including the live ``signal-emitted``, previews on both
  channels, whether or not the routing table fires that channel (R-5);
- the preview says whether the Telegram push rings or is silent;
- no preview shows a P&L word before certification;
- old event payloads, the committed JSON fixtures written before v2, still
  load and render through both templates (R-6, the migration test).

Reference: openspec/changes/144-notification-system/change.md (R-5, R-6).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from hermes.application.messages.reporting_phase import OUTCOME_FIELD_NAMES
from hermes.entrypoints import preview as preview_module

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "events"
_PNL_WORDS = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(OUTCOME_FIELD_NAMES)) + r"|profit|loss)\b",
    re.IGNORECASE,
)


def _run(argv: list[str]) -> tuple[int, str]:
    stream = io.StringIO()
    code = preview_module.main(argv, stream=stream)
    return code, stream.getvalue()


def _listed_events() -> list[str]:
    _, output = _run(["list"])
    return [line.split()[0] for line in output.splitlines() if line.startswith("  ")]


def test_list_includes_the_live_signal_events() -> None:
    """The catalog shows signal-emitted, the one event live in production, and its exit."""
    assert {"signal-emitted", "signal-exit"} <= set(_listed_events())


@pytest.mark.parametrize("event_name", _listed_events())
def test_every_event_previews_a_telegram_and_a_slack_section(event_name: str) -> None:
    """Each event prints a Telegram section and the full Slack section, routed or not."""
    code, output = _run([event_name])

    assert (
        code,
        "## Telegram (MarkdownV2)" in output,
        "## Slack — Block Kit" in output,
        "## Slack — attachments" in output,
    ) == (0, True, True, True)


def test_slack_only_event_says_telegram_has_no_template_instead_of_crashing() -> None:
    """The digest has no Telegram template; the preview says so and still renders Slack."""
    _, output = _run(["daily-trade-digest"])

    assert "Telegram has no template for this event" in output


def test_routed_event_with_a_template_on_both_channels_prints_the_telegram_message() -> None:
    """A signal prints the escaped compact message Telegram would receive."""
    _, output = _run(["signal-emitted"])

    assert "▲ Entry long BTC · 61,234\\.50 USDT" in output


@pytest.mark.parametrize(
    ("event_name", "push"),
    [
        ("balance-low", "loud (rings the phone)"),
        ("trade-rejected", "loud (rings the phone)"),
        ("strategy-paused", "loud (rings the phone)"),
        ("signal-emitted", "silent (disable_notification)"),
        ("position-large", "silent (disable_notification)"),
        ("rebalance-triggered", "silent (disable_notification)"),
    ],
)
def test_preview_states_whether_the_push_rings(event_name: str, push: str) -> None:
    """The preview prints loud for down, P0 and rejected orders and silent for the rest."""
    _, output = _run([event_name])

    assert f"Telegram push: {push}" in output


def test_rebalance_heads_up_is_now_routed_to_telegram() -> None:
    """The rebalance heads-up no longer reports that Telegram does not fire."""
    _, output = _run(["rebalance-triggered"])

    assert "Telegram does not fire" not in output


@pytest.mark.parametrize("event_name", _listed_events())
def test_no_preview_shows_a_pnl_word_before_certification(event_name: str) -> None:
    """The canned samples render with no P&L word in any section of any preview."""
    _, output = _run([event_name])

    assert _PNL_WORDS.findall(output) == []


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("signal-emitted", "Entry long BTC"),
        ("signal-exit", "Exit long BTC (trailing_stop)"),
        ("strategy-paused", "30% drawdown breached"),
        ("strategy-resumed", "back after 15 min"),
        ("balance-low", "50.00 USDT left"),
        ("trade-rejected", "binance: insufficient balance"),
        ("anomaly-detected", "swing.risk"),
        ("position-large", "BUY BTC"),
        ("rebalance-triggered", "drift 10.0 pp"),
        ("daily-trade-digest", "10/12 filled"),
    ],
)
def test_old_payload_renders_through_both_templates(fixture: str, expected: str) -> None:
    """A payload written before v2 loads and renders on both channels with its facts intact."""
    code, output = _run([fixture, "--fixture", str(_FIXTURES / f"{fixture}.json")])

    assert (code, expected in output.replace("\\", "")) == (0, True)


def test_every_committed_fixture_is_covered_by_the_migration_test() -> None:
    """A fixture added to tests/fixtures/events must be added to the migration parametrization."""
    covered = {
        "signal-emitted",
        "signal-exit",
        "strategy-paused",
        "strategy-resumed",
        "balance-low",
        "trade-rejected",
        "anomaly-detected",
        "position-large",
        "rebalance-triggered",
        "daily-trade-digest",
    }

    assert {path.stem for path in _FIXTURES.glob("*.json")} == covered
