"""Tests for the `hermes preview` CLI subcommand.

Verifies:
- ``list`` prints every event in the catalog.
- ``<event-name>`` renders the Telegram + Slack output for the canned sample.
- ``<event-name> --fixture <path>`` overrides the sample with JSON data.
- An unknown event name exits with code 2 and a diagnostic message.
- A missing fixture path exits with code 1.
- ``--send`` refuses to run when HERMES_ENV=prod (exit code 3).
- The routing table gates which channel-specific output blocks render.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from hermes.entrypoints import preview as preview_module

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "events"


class TestListCommand:
    """`hermes preview list` prints every event name and description."""

    def test_list_returns_zero(self) -> None:
        """The list command exits 0."""
        stream = io.StringIO()
        rc = preview_module.main(["list"], stream=stream)
        assert rc == 0

    def test_list_names_every_event(self) -> None:
        """Every event name in the catalog appears in the output."""
        stream = io.StringIO()
        preview_module.main(["list"], stream=stream)
        output = stream.getvalue()
        for name in [
            "strategy-paused",
            "strategy-resumed",
            "balance-low",
            "trade-rejected",
            "anomaly-detected",
            "position-large",
            "rebalance-triggered",
            "daily-trade-digest",
        ]:
            assert name in output

    def test_empty_argv_prints_list(self) -> None:
        """Invoking with no arguments is equivalent to `list`."""
        stream = io.StringIO()
        rc = preview_module.main([], stream=stream)
        assert rc == 0
        assert "strategy-paused" in stream.getvalue()


class TestPreviewRenderCommand:
    """`hermes preview <event>` renders both channel templates for a canned sample."""

    def test_strategy_paused_prints_telegram_and_slack_sections(self) -> None:
        """The output contains the Telegram section header and the Slack Block Kit header."""
        stream = io.StringIO()
        rc = preview_module.main(["strategy-paused"], stream=stream)
        output = stream.getvalue()
        assert rc == 0
        assert "## Telegram (MarkdownV2)" in output
        assert "## Slack — text fallback" in output
        assert "## Slack — Block Kit" in output

    def test_daily_trade_digest_previews_blind(self) -> None:
        """The digest preview shows what shadow sends: no P&L CSV and no P&L values."""
        stream = io.StringIO()
        rc = preview_module.main(["daily-trade-digest"], stream=stream)
        output = stream.getvalue()
        assert rc == 0
        assert "digest-2026-08-23.csv" not in output
        assert "120.00" not in output
        assert "## Slack — attachments\n(none)" in output

    def test_telegram_only_event_reports_slack_absence(self) -> None:
        """BALANCE_LOW is routed to Telegram only — the Slack section notes the routing."""
        stream = io.StringIO()
        rc = preview_module.main(["balance-low"], stream=stream)
        output = stream.getvalue()
        assert rc == 0
        assert "routing table: Slack does not fire" in output

    def test_slack_only_event_reports_telegram_absence(self) -> None:
        """DAILY_TRADE_DIGEST is routed to Slack only — the Telegram section notes the routing."""
        stream = io.StringIO()
        rc = preview_module.main(["daily-trade-digest"], stream=stream)
        output = stream.getvalue()
        assert rc == 0
        assert "routing table: Telegram does not fire" in output


class TestFixtureLoading:
    """`--fixture <path>` overrides the canned sample factory."""

    def test_loading_repo_fixture_renders_successfully(self) -> None:
        """Loading a committed fixture renders the same event name as the canned sample."""
        stream = io.StringIO()
        rc = preview_module.main(
            ["strategy-paused", "--fixture", str(_FIXTURES / "strategy-paused.json")],
            stream=stream,
        )
        output = stream.getvalue()
        assert rc == 0
        assert "STRATEGY\\_PAUSED" in output

    def test_missing_fixture_exits_one(self, tmp_path: Path) -> None:
        """A non-existent fixture path exits 1 with an ERROR line."""
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(
            ["strategy-paused", "--fixture", str(tmp_path / "missing.json")],
            stream=stream,
            err=err,
        )
        assert (rc, stream.getvalue()) == (1, "")
        assert "ERROR" in err.getvalue()

    def test_invalid_json_exits_one(self, tmp_path: Path) -> None:
        """A fixture whose content is not valid JSON exits 1 with an ERROR line."""
        bad = tmp_path / "bad.json"
        bad.write_text("not: valid json", encoding="utf-8")
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(["strategy-paused", "--fixture", str(bad)], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (1, "")
        assert "Invalid JSON" in err.getvalue()

    def test_non_object_json_exits_one(self, tmp_path: Path) -> None:
        """A fixture whose top-level is a JSON array (not object) exits 1."""
        arr = tmp_path / "arr.json"
        arr.write_text("[1, 2, 3]", encoding="utf-8")
        stream = io.StringIO()
        rc = preview_module.main(["strategy-paused", "--fixture", str(arr)], stream=stream)
        assert rc == 1

    def test_naive_timestamp_in_fixture_is_rejected(self, tmp_path: Path) -> None:
        """A fixture with a naive datetime string exits 1 — hermes rejects naive time."""
        naive = tmp_path / "naive.json"
        naive.write_text(
            '{"occurred_at": "2026-08-23T14:22:03", "correlation_id": "x", '
            '"strategy_id": "s", "reason": "r", '
            '"paused_at": "2026-08-23T14:22:03", "severity": "INFO"}',
            encoding="utf-8",
        )
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(
            ["strategy-paused", "--fixture", str(naive)], stream=stream, err=err
        )
        assert (rc, stream.getvalue()) == (1, "")
        assert "naive" in err.getvalue()

    @pytest.mark.parametrize(
        "event_name",
        [
            "strategy-paused",
            "strategy-resumed",
            "balance-low",
            "trade-rejected",
            "anomaly-detected",
            "position-large",
            "rebalance-triggered",
            "daily-trade-digest",
        ],
    )
    def test_every_repo_fixture_renders_without_error(self, event_name: str) -> None:
        """Every committed fixture reconstructs to a valid event and renders successfully."""
        stream = io.StringIO()
        rc = preview_module.main(
            [event_name, "--fixture", str(_FIXTURES / f"{event_name}.json")],
            stream=stream,
        )
        assert rc == 0
        assert f"# hermes preview — {event_name}" in stream.getvalue()


class TestUnknownEvent:
    """Unknown event names exit with code 2 and a diagnostic."""

    def test_unknown_event_exits_two(self) -> None:
        """An event name not in the catalog exits 2 with an ERROR line."""
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(["not-a-real-event"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (2, "")
        assert "unknown event name" in err.getvalue()


class TestSendGuard:
    """`--send` refuses to spam prod channels — exit code 3."""

    def test_send_refuses_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--send with HERMES_ENV=prod exits 3 without invoking any adapter."""
        monkeypatch.setenv("HERMES_ENV", "prod")
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(["strategy-paused", "--send"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (3, "")
        assert "HERMES_ENV=prod" in err.getvalue()

    def test_send_refuses_prod_case_insensitively(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--send with HERMES_ENV=PROD (uppercase) still triggers the refusal."""
        monkeypatch.setenv("HERMES_ENV", "PROD")
        stream = io.StringIO()
        rc = preview_module.main(["strategy-paused", "--send"], stream=stream)
        assert rc == 3

    def test_send_in_dev_env_reports_stub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--send in dev env exits 4 (scaffolding not yet implemented for send)."""
        monkeypatch.setenv("HERMES_ENV", "dev")
        stream, err = io.StringIO(), io.StringIO()
        rc = preview_module.main(["strategy-paused", "--send"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (4, "")
        assert "PRs B + C" in err.getvalue()


class TestBuildParser:
    """The argparse parser exposes the expected flags."""

    def test_parser_has_send_and_fixture_flags(self) -> None:
        """The parser accepts --send and --fixture."""
        parser = preview_module.build_parser()
        args = parser.parse_args(["strategy-paused", "--send", "--fixture", "some/path.json"])
        assert args.event == "strategy-paused"
        assert args.send is True
        assert args.fixture == Path("some/path.json")
