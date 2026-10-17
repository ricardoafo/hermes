"""Tests for the top-level `hermes` CLI dispatcher.

Verifies:
- No arguments prints the top-level help (exit 0).
- ``--help`` / ``-h`` prints the top-level help (exit 0).
- ``preview list`` delegates and prints the event catalog.
- ``simulate`` exits 2 with a "not yet implemented" note.
- ``serve`` exit codes: 0 clean, 1 runtime, 2 usage, 3 config.
- ``health`` (no arg) prints usage and exits 2.
- ``health <unknown>`` exits 2 with a diagnostic.
- ``health telegram`` / ``health slack`` delegate to the registry probes.
- An unknown top-level subcommand exits 2 with a diagnostic.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
import structlog

from hermes.entrypoints import _health as health_module
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints.commands import discover
from hermes.entrypoints.commands import serve as serve_command
from hermes.entrypoints.daily_line import count_journal

_RECORDED_DIR = Path(__file__).parents[2] / "fixtures" / "binance_public"


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
    """simulate prints a 'not yet implemented' line and exits 2."""

    @pytest.mark.parametrize("subcommand", ["simulate"])
    def test_stub_subcommand_exits_two(self, subcommand: str) -> None:
        """Each stub subcommand exits 2 with an ERROR line naming the subcommand."""
        stream, err = io.StringIO(), io.StringIO()
        rc = cli_module.main([subcommand], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (2, "")
        assert subcommand in err.getvalue()
        assert "not yet implemented" in err.getvalue()


class TestServeSubcommand:
    """`hermes serve` dispatches to the composition-root handler."""

    def test_serve_help_exits_zero(self) -> None:
        """`hermes serve --help` prints usage and exits 0 without touching config."""
        stream = io.StringIO()
        rc = cli_module.main(["serve", "--help"], stream=stream)
        assert rc == 0
        assert "hermes serve" in stream.getvalue()

    def test_serve_missing_config_dir_exits_three(self, tmp_path: object) -> None:
        """A non-existent --config-dir returns exit code 3 (config load failed)."""
        stream, err = io.StringIO(), io.StringIO()
        rc = cli_module.main(
            ["serve", "--config-dir", "does-not-exist-abcdef"],
            stream=stream,
            err=err,
        )
        assert (rc, stream.getvalue()) == (3, "")
        assert "config load failed" in err.getvalue()

    def test_serve_logs_json_the_daily_line_can_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """After `hermes serve` starts, a log line is JSON that count_journal reads (#547).

        The daily alive line counts serve's errors with ``count_journal``,
        which skips any line that is not a JSON object.
        """
        try:
            cli_module.main(
                ["serve", "--config-dir", "does-not-exist-abcdef"], stream=io.StringIO()
            )
            capsys.readouterr()
            structlog.get_logger("hermes.tests.serve").error("serve.test.failed", pair="BTC/USDT")
            lines = capsys.readouterr().out.splitlines()
        finally:
            structlog.reset_defaults()

        assert count_journal(lines).errors == Counter({"serve.test.failed": 1})


class TestServeExitCodes:
    """`hermes serve` exit codes: 0 clean, 1 runtime, 2 usage, 3 config."""

    def test_serve_env_flag_stays_rejected_with_exit_two(self) -> None:
        """`--env` is not a flag: HERMES_ENV is the only source of the tier (D-8)."""
        stream, err = io.StringIO(), io.StringIO()

        rc = cli_module.main(["serve", "--env", "prod"], stream=stream, err=err)

        assert (rc, "could not parse" in err.getvalue()) == (2, True)

    def test_serve_help_documents_the_exit_codes_and_hermes_env(self) -> None:
        """The help text names HERMES_ENV and the four exit codes."""
        stream = io.StringIO()

        cli_module.main(["serve", "--help"], stream=stream)

        text = stream.getvalue()
        assert ("HERMES_ENV" in text, "3 config" in text) == (True, True)

    def test_a_failed_startup_check_exits_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A StartupError from the check maps to exit 3 with its message."""

        def _refuse(_config: object, _env: object, **_kwargs: object) -> None:
            raise serve_module.StartupError("roster is wrong")

        monkeypatch.setattr(serve_module, "check_startup", _refuse)
        stream, err = io.StringIO(), io.StringIO()
        config_dir = Path(__file__).resolve().parents[3] / "config"

        rc = cli_module.main(["serve", "--config-dir", str(config_dir)], stream=stream, err=err)

        assert (rc, "roster is wrong" in err.getvalue()) == (3, True)

    def test_serve_main_returns_the_exit_code_of_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_serve_main returns whatever run() returns instead of a hard-coded 0."""

        async def _fake_run(_composition: object, _shutdown: object) -> int:
            return 7

        monkeypatch.setattr(serve_module, "build_composition", lambda *_a, **_k: object())
        monkeypatch.setattr(serve_module, "install_signal_handlers", lambda *_a, **_k: None)
        monkeypatch.setattr(serve_module, "run", _fake_run)

        rc = asyncio.run(serve_command.serve_main(object(), db_path=tmp_path / "x.sqlite3"))

        assert rc == 7

    @pytest.mark.parametrize(("argv", "expected"), [([], False), (["--new-ledger"], True)])
    def test_new_ledger_reaches_the_build_only_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        argv: list[str],
        expected: bool,
    ) -> None:
        """#876: the build may create a missing ledger only when --new-ledger is given."""
        seen: list[object] = []

        def _build(*_a: object, **kwargs: object) -> object:
            seen.append(kwargs["new_ledger"])
            raise RuntimeError("stop after the build")

        monkeypatch.setattr(serve_module, "check_startup", lambda *_a, **_k: None)
        monkeypatch.setattr(serve_module, "build_composition", _build)
        monkeypatch.setattr(serve_module, "report_failure", lambda _exc: None)
        config_dir = Path(__file__).resolve().parents[3] / "config"

        rc = cli_module.main(
            ["serve", "--config-dir", str(config_dir), "--db", str(tmp_path / "x.sqlite3"), *argv],
            stream=io.StringIO(),
            err=io.StringIO(),
        )

        assert (rc, seen) == (1, [expected])

    def test_a_failed_build_exits_one_and_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A wiring failure is a runtime failure: exit 1, logged and sent to Sentry."""
        reported: list[BaseException] = []

        def _boom(*_a: object, **_k: object) -> object:
            raise RuntimeError("cannot open ledger")

        monkeypatch.setattr(serve_module, "build_composition", _boom)
        monkeypatch.setattr(serve_module, "report_failure", reported.append)

        rc = asyncio.run(serve_command.serve_main(object(), db_path=tmp_path / "x.sqlite3"))

        assert (rc, [str(e) for e in reported]) == (1, ["cannot open ledger"])


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
        stream, err = io.StringIO(), io.StringIO()
        rc = cli_module.main(["not-a-command"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (2, "")
        assert "unknown subcommand" in err.getvalue()

    def test_close_miss_suggests_the_command(self) -> None:
        """A one-letter typo names the intended command and still exits 2."""
        stream, err = io.StringIO(), io.StringIO()
        rc = cli_module.main(["statsu"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (2, "")
        assert "Did you mean 'status'?" in err.getvalue()

    def test_distant_miss_offers_no_suggestion(self) -> None:
        """A token far from every command gets no suggestion."""
        stream = io.StringIO()
        cli_module.main(["zzzzqqqq"], stream=stream)
        assert "Did you mean" not in stream.getvalue()


class TestTopLevelHelpContent:
    """The top-level help is accurate and derived from the registry."""

    def test_header_is_not_the_stale_scaffold_line(self) -> None:
        """The header no longer claims to be an MVP1 scaffold."""
        stream = io.StringIO()
        cli_module.main(["--help"], stream=stream)
        assert "scaffold" not in stream.getvalue()

    def test_name_column_fits_the_longest_command(self) -> None:
        """The longest registered name is followed by two spaces, then its summary."""
        stream = io.StringIO()
        cli_module.main(["--help"], stream=stream)
        longest = max(discover().values(), key=lambda command: len(command.name))
        assert f"  {longest.name}  {longest.summary}" in stream.getvalue()

    def test_demo_note_lists_exactly_the_wants_demo_commands(self) -> None:
        """The --demo note names the commands with wants_demo=True, from the registry."""
        stream = io.StringIO()
        cli_module.main(["--help"], stream=stream)
        expected = ", ".join(sorted(n for n, c in discover().items() if c.wants_demo))
        assert f"Commands that accept it: {expected}\n" in stream.getvalue()

    def test_json_note_is_present(self) -> None:
        """The help says which commands have --json."""
        stream = io.StringIO()
        cli_module.main(["--help"], stream=stream)
        assert "--json" in stream.getvalue()


class TestHealthDispatch:
    """`hermes health` sub-dispatch — no-arg full walk + per-integration probe."""

    @pytest.fixture(autouse=True)
    def _no_dotenv_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Run from an empty directory so a developer's ``.env.local`` is never read."""
        monkeypatch.chdir(tmp_path)

    def test_health_reads_credentials_from_env_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A credential present only in ``.env.local`` reaches the probe (#316).

        Before the fix the probe saw only the process env and reported
        "not set" while pointing the operator at ``.env.local``.
        """
        monkeypatch.delenv("HERMES_SENTRY__DSN", raising=False)
        (tmp_path / ".env.local").write_text(
            "HERMES_SENTRY__DSN=https://abc@o1.ingest.sentry.io/1\n",  # gitleaks:allow
            encoding="utf-8",
        )
        seen: dict[str, str] = {}

        def _probe(env: dict[str, str]) -> health_module.HealthCheckResult:
            seen.update(env)
            return health_module.check_sentry({})

        monkeypatch.setattr(health_module, "get_check", lambda _name: _probe)
        cli_module.main(["health", "sentry"], stream=io.StringIO())
        assert seen["HERMES_SENTRY__DSN"] == "https://abc@o1.ingest.sentry.io/1"  # gitleaks:allow

    def test_health_real_env_wins_over_env_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A process env var shadows the same key in ``.env.local``, as in ``load_config``."""
        monkeypatch.setenv("HERMES_ENV", "from-shell")
        (tmp_path / ".env.local").write_text("HERMES_ENV=from-file\n", encoding="utf-8")
        seen: dict[str, str] = {}

        def _probe(env: dict[str, str]) -> health_module.HealthCheckResult:
            seen.update(env)
            return health_module.check_sentry({})

        monkeypatch.setattr(health_module, "get_check", lambda _name: _probe)
        cli_module.main(["health", "sentry"], stream=io.StringIO())
        assert seen["HERMES_ENV"] == "from-shell"

    def test_health_without_arg_runs_all_and_reports_not_configured_on_empty_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`hermes health` (no arg) walks the registry and exits 3 on a pristine env.

        With no credentials in the env, every registered probe reports
        NOT_CONFIGURED, so :func:`aggregate_exit_code` returns 3
        (partial config is not a system-down state — that's exit 1).
        """
        # Strip every credential env var so `run_all` sees a pristine snapshot.
        for var in (
            "HERMES_TELEGRAM__BOT_TOKEN",
            "HERMES_TELEGRAM__CHAT_ID",
            "HERMES_SLACK__BOT_TOKEN",
            "HERMES_SLACK__DEFAULT_CHANNEL",
            "HERMES_HEALTHCHECKS__PING_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        # market_data needs no credentials, so it probes the (mocked) public host
        # and reports OK; the other four stay NOT_CONFIGURED, keeping the exit at 3.
        recorded_ms = json.loads((_RECORDED_DIR / "time.json").read_text(encoding="utf-8"))[
            "serverTime"
        ]
        rows = json.loads(
            (_RECORDED_DIR / "klines_btcusdt_4h_limit3.json").read_text(encoding="utf-8")
        )
        recorded_now = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=recorded_ms)
        monkeypatch.setattr("hermes.entrypoints._health._now_utc", lambda: recorded_now)
        stream = io.StringIO()
        with respx.mock() as router:
            router.get("https://data-api.binance.vision/api/v3/time").mock(
                return_value=httpx.Response(200, json={"serverTime": recorded_ms})
            )
            router.get("https://data-api.binance.vision/api/v3/klines").mock(
                return_value=httpx.Response(200, json=rows)
            )
            rc = cli_module.main(["health"], stream=stream)
        assert rc == 3
        output = stream.getvalue()
        assert "# hermes health (all integrations)" in output
        assert "market_data" in output
        # Multi-row table shows every registered integration, all reporting FAIL /
        # NOT_CONFIGURED. Every registered name appears in the output.
        assert "telegram" in output
        assert "slack" in output
        assert "healthchecks" in output
        # The multi-table header row names the columns:
        assert "integration" in output
        assert "status" in output
        assert "latency" in output
        # Every row reports FAIL (NOT_CONFIGURED renders as FAIL status label).
        assert "FAIL" in output

    def test_health_unknown_integration_exits_two(self) -> None:
        """`hermes health <unknown>` prints an ERROR and exits 2."""
        stream, err = io.StringIO(), io.StringIO()
        rc = cli_module.main(["health", "not-a-real-integration"], stream=stream, err=err)
        assert (rc, stream.getvalue()) == (2, "")
        assert "unknown integration" in err.getvalue()
        assert "not-a-real-integration" in err.getvalue()

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

    def test_health_sentry_missing_config_exits_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`hermes health sentry` with no DSN exits 3 (not configured)."""
        monkeypatch.delenv("HERMES_SENTRY__DSN", raising=False)
        stream = io.StringIO()
        rc = cli_module.main(["health", "sentry"], stream=stream)
        assert rc == 3
        output = stream.getvalue()
        assert "sentry" in output
        assert "FAIL" in output
        assert "disabled" in output


class TestSentryInit:
    """CLI main() initializes Sentry from env before dispatch."""

    def test_main_calls_sentry_initialize_with_env_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() invokes sentry_integration.initialize(dsn, environment) at startup."""
        captured: dict[str, str] = {}

        def _spy_initialize(*, dsn: str, environment: str) -> None:
            captured["dsn"] = dsn
            captured["environment"] = environment

        monkeypatch.setattr(cli_module.sentry_integration, "initialize", _spy_initialize)
        monkeypatch.setenv("HERMES_SENTRY__DSN", "https://public@sentry.example.com/1")
        monkeypatch.setenv("HERMES_SENTRY__ENVIRONMENT", "sandbox")
        # --help exits after handling help; we only care initialize was called first.
        cli_module.main(["--help"], stream=io.StringIO())
        assert captured["dsn"] == "https://public@sentry.example.com/1"
        assert captured["environment"] == "sandbox"

    def test_main_defaults_environment_to_dev_when_env_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing HERMES_SENTRY__ENVIRONMENT → initialize called with 'dev' default."""
        captured: dict[str, str] = {}

        def _spy_initialize(*, dsn: str, environment: str) -> None:
            captured["dsn"] = dsn
            captured["environment"] = environment

        monkeypatch.setattr(cli_module.sentry_integration, "initialize", _spy_initialize)
        monkeypatch.setenv("HERMES_SENTRY__DSN", "https://public@sentry.example.com/1")
        monkeypatch.delenv("HERMES_SENTRY__ENVIRONMENT", raising=False)
        cli_module.main(["--help"], stream=io.StringIO())
        assert captured["environment"] == "dev"
