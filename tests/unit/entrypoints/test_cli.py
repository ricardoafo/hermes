"""Tests for the top-level `hermes` CLI dispatcher.

Verifies:
- No arguments prints the top-level help (exit 0).
- ``--help`` / ``-h`` prints the top-level help (exit 0).
- ``preview list`` delegates and prints the event catalog.
- ``serve`` / ``simulate`` exit 2 with a "not yet implemented" note.
- ``health`` (no arg) prints usage and exits 2.
- ``health <unknown>`` exits 2 with a diagnostic.
- ``health telegram`` / ``health slack`` delegate to the registry probes.
- An unknown top-level subcommand exits 2 with a diagnostic.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import io
import sys

import pytest

from hermes.entrypoints import cli as cli_module


class TestTopLevelHelp:
    """No args or --help prints the help banner and exits zero."""

    def test_no_args_prints_usage(self) -> None:
        """Invoking with an empty argv prints the usage line and exits 0."""
        stream = io.StringIO()
        rc = cli_module.main([], stream=stream)
        assert rc == 0
        assert "Usage: hermes" in stream.getvalue()

    def test_help_flag_prints_usage(self) -> None:
        """The --help flag prints the usage line and exits 0."""
        stream = io.StringIO()
        rc = cli_module.main(["--help"], stream=stream)
        assert rc == 0
        assert "Usage: hermes" in stream.getvalue()

    def test_short_help_flag_prints_usage(self) -> None:
        """The -h short flag prints the usage line and exits 0."""
        stream = io.StringIO()
        rc = cli_module.main(["-h"], stream=stream)
        assert rc == 0
        assert "Usage: hermes" in stream.getvalue()


class TestPreviewDelegation:
    """`preview` dispatches to the preview subcommand."""

    def test_preview_list_prints_catalog(self) -> None:
        """`hermes preview list` prints the event catalog through the delegate."""
        stream = io.StringIO()
        rc = cli_module.main(["preview", "list"], stream=stream)
        assert rc == 0
        assert "strategy-paused" in stream.getvalue()


class TestStubSubcommands:
    """serve / simulate print a 'not yet implemented' line and exit 2."""

    @pytest.mark.parametrize("subcommand", ["serve", "simulate"])
    def test_stub_subcommand_exits_two(self, subcommand: str) -> None:
        """Each stub subcommand exits 2 with an ERROR line naming the subcommand."""
        stream = io.StringIO()
        rc = cli_module.main([subcommand], stream=stream)
        assert rc == 2
        output = stream.getvalue()
        assert subcommand in output
        assert "not yet implemented" in output


class TestMainDefaultStream:
    """When stream is None, main writes to (a reconfigured) sys.stdout."""

    def test_main_uses_sys_stdout_when_stream_is_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Invoking main with stream=None writes the help banner to captured stdout."""
        rc = cli_module.main([])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Usage: hermes" in captured.out


class TestUtf8Stdout:
    """The stdout helper reconfigures the stream when possible."""

    def test_reconfigure_is_invoked_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_utf8_stdout calls reconfigure(encoding='utf-8') on streams that expose it."""
        recorded: dict[str, object] = {}

        class _FakeStream:
            def reconfigure(self, *, encoding: str) -> None:
                recorded["encoding"] = encoding

            def write(self, _text: str) -> None:  # pragma: no cover — unused
                pass

        fake = _FakeStream()
        monkeypatch.setattr(sys, "stdout", fake)
        result = cli_module._utf8_stdout()
        assert result is fake
        assert recorded["encoding"] == "utf-8"

    def test_reconfigure_absent_returns_stream_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_utf8_stdout tolerates a stream without a reconfigure method."""

        class _MinimalStream:
            def write(self, _text: str) -> None:  # pragma: no cover — unused
                pass

        minimal = _MinimalStream()
        monkeypatch.setattr(sys, "stdout", minimal)
        result = cli_module._utf8_stdout()
        assert result is minimal


class TestUnknownSubcommand:
    """Unknown subcommands exit 2 with a diagnostic."""

    def test_unknown_subcommand_exits_two(self) -> None:
        """An unknown top-level subcommand exits 2 and mentions --help."""
        stream = io.StringIO()
        rc = cli_module.main(["not-a-command"], stream=stream)
        assert rc == 2
        assert "unknown subcommand" in stream.getvalue()


class TestHealthDispatch:
    """`hermes health` sub-dispatch — no-arg usage + per-integration probe."""

    def test_health_without_arg_prints_usage_and_exits_two(self) -> None:
        """`hermes health` (no arg) prints usage + registered integrations, exits 2."""
        stream = io.StringIO()
        rc = cli_module.main(["health"], stream=stream)
        assert rc == 2
        output = stream.getvalue()
        assert "Usage: hermes health <integration>" in output
        # Both integrations appear in the registry listing.
        assert "telegram" in output
        assert "slack" in output

    def test_health_unknown_integration_exits_two(self) -> None:
        """`hermes health <unknown>` prints an ERROR and exits 2."""
        stream = io.StringIO()
        rc = cli_module.main(["health", "not-a-real-integration"], stream=stream)
        assert rc == 2
        output = stream.getvalue()
        assert "unknown integration" in output
        assert "not-a-real-integration" in output

    def test_health_telegram_missing_config_exits_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`hermes health telegram` with no bot-token env var exits 3 (not configured)."""
        # Strip both required env vars — dict(os.environ) inside the CLI must not see them.
        monkeypatch.delenv("HERMES_TELEGRAM__BOT_TOKEN", raising=False)
        monkeypatch.delenv("HERMES_TELEGRAM__CHAT_ID", raising=False)
        stream = io.StringIO()
        rc = cli_module.main(["health", "telegram"], stream=stream)
        assert rc == 3
        output = stream.getvalue()
        assert "telegram" in output
        assert "FAIL" in output
        assert ".env.local" in output

    def test_health_slack_missing_config_exits_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`hermes health slack` with no bot-token env var exits 3 (not configured)."""
        # Strip both required env vars — dict(os.environ) inside the CLI must not see them.
        monkeypatch.delenv("HERMES_SLACK__BOT_TOKEN", raising=False)
        monkeypatch.delenv("HERMES_SLACK__DEFAULT_CHANNEL", raising=False)
        stream = io.StringIO()
        rc = cli_module.main(["health", "slack"], stream=stream)
        assert rc == 3
        output = stream.getvalue()
        assert "slack" in output
        assert "FAIL" in output
        assert ".env.local" in output
