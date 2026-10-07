"""Behaviour of the two alerting scripts shipped in ``deploy/`` (issue #312).

``hermes-health-ping.sh`` and ``hermes-notify-failure@.service``'s script run
on the VPS and nothing in CI starts them, so these tests run the real scripts
under bash with the real ``curl`` against a loopback HTTP server. The only
stand-ins are ``systemctl`` and ``journalctl`` (they need a live systemd) and
a ``curl`` wrapper that records its argv, then execs the real binary.

The scripts need only bash and curl; the tests skip when either is missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

_DEPLOY_DIR = Path(__file__).resolve().parents[3] / "deploy"
_BASH = shutil.which("bash")
_CURL = shutil.which("curl")

pytestmark = pytest.mark.skipif(
    _BASH is None or _CURL is None, reason="the alert scripts need bash and curl"
)

# Obviously fake values shaped like the real ones.
_BOT_TOKEN = "123456789:FAKE-bot-token_for_tests"  # noqa: S105 - test-only token  # gitleaks:allow
_CHAT_ID = "-1001234567890"
_PING_PATH = "/ping/00000000-fake-uuid"

_STUB_SYSTEMCTL = """#!/usr/bin/env bash
here="$(dirname "$0")"
if [ "$*" != "is-active --quiet hermes.service" ]; then
    echo "unexpected systemctl call: $*" >&2
    exit 99
fi
exit "$(cat "$here/systemctl.rc")"
"""

_STUB_JOURNALCTL = """#!/usr/bin/env bash
here="$(dirname "$0")"
unit=""
lines=1000
for arg in "$@"; do
    case "$arg" in
        --unit=*) unit="${arg#--unit=}" ;;
        --lines=*) lines="${arg#--lines=}" ;;
    esac
done
if [ -e "$here/journal.unreadable" ]; then
    echo "No journal files were opened due to insufficient permissions." >&2
    exit 1
fi
[ "$unit" = "$(cat "$here/journal.unit")" ] || exit 0
tail -n "$lines" "$here/journal.txt"
"""

_FAKE_HERMES = """#!/usr/bin/env bash
here="$(dirname "$0")"
if [ "${2:-}" = "--help" ]; then
    exit "$(cat "$here/hermes.help.rc")"
fi
printf '%s\\n' "$@" > "$here/hermes.argv"
cat "$here/hermes.out"
exit "$(cat "$here/hermes.rc")"
"""

_STATUS_OK = '{"verdict": {"level": "ok", "headline": "All good"}}'
_STATUS_LATE = '{"verdict": {"level": "late", "headline": "The 22:00 check is 20 minutes late"}}'

_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


@dataclass
class _Received:
    method: str
    path: str
    body: str
    authorization: str = ""


@dataclass
class _FakeServer:
    """Loopback HTTP endpoint that records every request and answers ``status``."""

    url: str
    requests: list[_Received] = field(default_factory=list)
    status: int = 200
    response: bytes = b"ok"


@pytest.fixture
def server() -> Iterator[_FakeServer]:
    """Serve on a free loopback port; every request is recorded on the fixture."""
    fake = _FakeServer(url="")

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            authorization = self.headers.get("Authorization") or ""
            fake.requests.append(_Received(self.command, self.path, body, authorization))
            self.send_response(fake.status)
            self.send_header("Content-Length", str(len(fake.response)))
            self.end_headers()
            self.wfile.write(fake.response)

        do_GET = _record  # noqa: N815
        do_POST = _record  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            """Keep pytest output quiet."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    # The default 0.5s poll makes every teardown's shutdown() wait that long.
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


class _Host:
    """Scratch ``PATH`` directory holding the stand-ins for systemd's tools."""

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self._write_exe("systemctl", _STUB_SYSTEMCTL)
        self._write_exe("journalctl", _STUB_JOURNALCTL)
        self._write_exe("curl", _SPY_CURL)
        self._write_exe("hermes", _FAKE_HERMES)
        self.set_hermes_active(True)
        self.set_status(0, _STATUS_OK)
        self.set_has_status(True)
        self.set_journal([])

    def _write_exe(self, name: str, text: str) -> None:
        path = self.bin_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def _write(self, name: str, text: str) -> None:
        (self.bin_dir / name).write_text(text, encoding="utf-8", newline="\n")

    def set_hermes_active(self, active: bool) -> None:
        """`systemctl is-active --quiet hermes.service` exits 0 when active, 3 when not."""
        self._write("systemctl.rc", "0" if active else "3")

    def set_status(self, rc: int, output: str) -> None:
        """Make the fake `hermes status --json` print ``output`` and exit ``rc``."""
        self._write("hermes.rc", str(rc))
        self._write("hermes.out", output)

    def set_has_status(self, has: bool) -> None:
        """`hermes status --help` exits 0 when the release has the command, 2 when not."""
        self._write("hermes.help.rc", "0" if has else "2")

    def hermes_argv(self) -> list[str]:
        """Return the argv the ping script gave `hermes`."""
        return (self.bin_dir / "hermes.argv").read_text(encoding="utf-8").splitlines()

    def set_journal(self, lines: list[str], unit: str = "hermes.service") -> None:
        """Make `journalctl --unit=<unit>` answer with ``lines`` (honouring --lines)."""
        self._write("journal.txt", "".join(f"{line}\n" for line in lines))
        self._write("journal.unit", unit)

    def make_journal_unreadable(self) -> None:
        """Make journalctl fail the way it does for a user outside systemd-journal."""
        self._write("journal.unreadable", "")

    def curl_argv(self) -> str:
        """Return the argv the scripts gave curl, one argument per line."""
        return (self.bin_dir / "curl.argv").read_text(encoding="utf-8")


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    """Return a scratch PATH directory with the systemd stand-ins installed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return _Host(bin_dir)


def _run(
    script: str,
    host: _Host,
    server: _FakeServer,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``deploy/<script>`` with the stand-ins first on PATH and the env file's variables set."""
    assert _BASH is not None
    assert _CURL is not None
    environ = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("HERMES_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    }
    environ["PATH"] = f"{host.bin_dir}{os.pathsep}{environ['PATH']}"
    environ["REAL_CURL"] = _CURL
    environ["NO_PROXY"] = "*"
    environ.update(
        {
            "HERMES_TELEGRAM__BOT_TOKEN": _BOT_TOKEN,
            "HERMES_TELEGRAM__CHAT_ID": _CHAT_ID,
            "HERMES_NOTIFY_API_BASE": server.url,
            "HERMES_HEALTHCHECKS__PING_URL": f"{server.url}{_PING_PATH}",
            "HERMES_HEALTH_HERMES_BIN": (host.bin_dir / "hermes").as_posix(),
        }
    )
    environ.update(env or {})
    return subprocess.run(  # noqa: S603
        [_BASH, (_DEPLOY_DIR / script).as_posix(), *args],
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _sent_text(server: _FakeServer) -> str:
    """Return the ``text`` field of the single sendMessage request the server saw."""
    assert len(server.requests) == 1
    return parse_qs(server.requests[0].body)["text"][0]


# integration: none feasible in CI (the stand-in systemctl/journalctl replace a live systemd);
# the operator check is `systemctl start hermes-notify-failure@hermes.service` (deploy/README.md).
_NOTIFY = "hermes-notify-failure.sh"
_PING = "hermes-health-ping.sh"


@pytest.mark.parametrize("args", [["hermes.service"], []], ids=["explicit-unit", "default-unit"])
def test_notifier_sends_exactly_one_message_naming_the_unit_and_host(
    host: _Host, server: _FakeServer, args: list[str]
) -> None:
    """A failure produces one sendMessage POST whose first line names the unit and the host."""
    result = _run(_NOTIFY, host, server, *args)

    assert result.returncode == 0, result.stderr
    assert [(r.method, r.path) for r in server.requests] == [
        ("POST", f"/bot{_BOT_TOKEN}/sendMessage")
    ]
    assert parse_qs(server.requests[0].body)["chat_id"] == [_CHAT_ID]
    first_line = _sent_text(server).splitlines()[0]
    assert re.fullmatch(r"hermes\.service entered failed state on \S+", first_line)


def test_notifier_names_the_unit_it_was_started_for(host: _Host, server: _FakeServer) -> None:
    """The instance name passed by systemd (%I) is the unit in the text and the journal query."""
    host.set_journal(["backup went wrong"], unit="hermes-backup.service")

    result = _run(_NOTIFY, host, server, "hermes-backup.service")

    assert result.returncode == 0, result.stderr
    text = _sent_text(server)
    assert text.startswith("hermes-backup.service entered failed state on ")
    assert text.endswith("Last journal lines:\nbackup went wrong")


def test_notifier_keeps_the_bot_token_out_of_the_curl_argv(
    host: _Host, server: _FakeServer
) -> None:
    """`ps` shows argv to every local user, so the token may only travel on curl's stdin."""
    result = _run(_NOTIFY, host, server)

    assert result.returncode == 0, result.stderr
    assert server.requests[0].path == f"/bot{_BOT_TOKEN}/sendMessage"
    assert _BOT_TOKEN not in host.curl_argv()


def test_notifier_appends_only_the_last_five_journal_lines(
    host: _Host, server: _FakeServer
) -> None:
    """The message carries the tail of the journal, capped at five lines, oldest first."""
    host.set_journal([f"journal line {n}" for n in range(1, 9)])

    result = _run(_NOTIFY, host, server)

    assert result.returncode == 0, result.stderr
    _, _, tail = _sent_text(server).partition("\n\n")
    assert tail == "Last journal lines:\n" + "\n".join(f"journal line {n}" for n in range(4, 9))


def test_notifier_truncates_a_long_journal_line_to_300_characters(
    host: _Host, server: _FakeServer
) -> None:
    """One huge traceback line must not blow past Telegram's 4096-character limit."""
    host.set_journal(["x" * 1000])

    result = _run(_NOTIFY, host, server)

    assert result.returncode == 0, result.stderr
    assert _sent_text(server).splitlines()[-1] == "x" * 300


def test_notifier_delivers_quotes_backslashes_and_tabs_in_journal_lines_unchanged(
    host: _Host, server: _FakeServer
) -> None:
    """Quotes, backslashes and tabs must survive the curl config the text rides in."""
    line = 'KeyError: "price" in C:\\data\\bars\tcolumn'
    host.set_journal([line])

    result = _run(_NOTIFY, host, server)

    assert result.returncode == 0, result.stderr
    assert _sent_text(server).splitlines()[-1] == line


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("HERMES_TELEGRAM__BOT_TOKEN", _BOT_TOKEN),
        ("HERMES_SLACK__BOT_TOKEN", "xoxb-FAKE-slack-token-for-tests"),  # gitleaks:allow
        ("HERMES_SENTRY__DSN", "https://fakekey@sentry.example.com/1"),  # gitleaks:allow
        ("HERMES_HEALTHCHECKS__PING_URL", "https://hc-ping.com/fake-uuid-for-tests"),
    ],
)
def test_notifier_redacts_secret_values_found_in_journal_lines(
    host: _Host, server: _FakeServer, name: str, secret: str
) -> None:
    """A secret the engine logged must not be forwarded to Telegram."""
    host.set_journal([f"httpx: POST {secret} failed"])

    result = _run(_NOTIFY, host, server, env={name: secret})

    assert result.returncode == 0, result.stderr
    assert _sent_text(server).splitlines()[-1] == "httpx: POST [redacted] failed"


def test_notifier_does_not_redact_short_non_secret_values(host: _Host, server: _FakeServer) -> None:
    """Values like HERMES_ENV=prod are not secrets; masking them would shred ordinary text."""
    host.set_journal(["serve.run.started env=prod"])

    result = _run(_NOTIFY, host, server, env={"HERMES_SOME_KEY": "prod"})

    assert result.returncode == 0, result.stderr
    assert _sent_text(server).splitlines()[-1] == "serve.run.started env=prod"


def test_notifier_still_sends_the_alert_when_the_journal_is_unreadable(
    host: _Host, server: _FakeServer
) -> None:
    """Journal access is a bonus: a failed journalctl must never cost the operator the alert."""
    host.make_journal_unreadable()

    result = _run(_NOTIFY, host, server)

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"hermes\.service entered failed state on \S+", _sent_text(server))


def test_notifier_fails_with_curl_status_22_when_telegram_rejects_the_message(
    host: _Host, server: _FakeServer
) -> None:
    """A rejected alert (bad token, bad chat id) must show up as a failed unit, not as success."""
    server.status = 401

    result = _run(_NOTIFY, host, server)

    assert result.returncode == 22
    assert "401" in result.stderr


@pytest.mark.parametrize("missing", ["HERMES_TELEGRAM__BOT_TOKEN", "HERMES_TELEGRAM__CHAT_ID"])
def test_notifier_without_credentials_exits_1_and_sends_nothing(
    host: _Host, server: _FakeServer, missing: str
) -> None:
    """Missing credentials are reported by name instead of posting to a half-built URL."""
    result = _run(_NOTIFY, host, server, env={missing: ""})

    assert result.returncode == 1
    assert missing in result.stderr
    assert server.requests == []


def test_health_ping_reports_all_good_as_success_with_the_status_body(
    host: _Host, server: _FakeServer
) -> None:
    """All good: one success ping per tick, carrying the status JSON for the check's log."""
    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.method, r.path, r.body) for r in server.requests] == [
        ("POST", _PING_PATH, _STATUS_OK)
    ]


def test_health_ping_reports_a_late_check_as_fail_with_the_status_body(
    host: _Host, server: _FakeServer
) -> None:
    """A running but stalled loop must turn the check red, not stay green (#372)."""
    host.set_status(1, _STATUS_LATE)

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.method, r.path, r.body) for r in server.requests] == [
        ("POST", f"{_PING_PATH}/fail", _STATUS_LATE)
    ]


def test_health_ping_reports_a_missing_ledger_as_fail_with_the_error(
    host: _Host, server: _FakeServer
) -> None:
    """Exit 3 (no ledger, config error) is a problem to page about, with its message."""
    host.set_status(3, "ERROR: config load failed: boom")

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.path, r.body) for r in server.requests] == [
        (f"{_PING_PATH}/fail", "ERROR: config load failed: boom")
    ]


def test_health_ping_falls_back_to_a_plain_success_on_a_release_without_status(
    host: _Host, server: _FakeServer
) -> None:
    """Exit 2 (no `status` command yet) keeps the pre-#372 behaviour during the rollout."""
    host.set_has_status(False)
    host.set_status(2, "ERROR: unknown command")

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.path, r.body) for r in server.requests] == [(_PING_PATH, "")]


def test_health_ping_reports_a_status_argument_error_as_fail_with_the_error(
    host: _Host, server: _FakeServer
) -> None:
    """Exit 2 from a release that has `status` is a broken call, not an old release (#856)."""
    host.set_status(2, "error: unrecognized arguments: --json")

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.path, r.body) for r in server.requests] == [
        (f"{_PING_PATH}/fail", "error: unrecognized arguments: --json")
    ]


def test_health_ping_asks_hermes_status_for_json_on_the_release_paths(
    host: _Host, server: _FakeServer
) -> None:
    """The ping reads the prod ledger and config through `hermes status --json`."""
    _run(_PING, host, server)

    assert host.hermes_argv() == [
        "status",
        "--config-dir",
        "/opt/hermes/current/config",
        "--db",
        "/var/lib/hermes/hermes.sqlite3",
        "--json",
    ]


def test_health_ping_is_skipped_while_hermes_is_not_active(
    host: _Host, server: _FakeServer
) -> None:
    """No ping while the service is down: the missed ping is what alerts the operator."""
    host.set_hermes_active(False)

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert server.requests == []
    assert "ping skipped" in result.stdout
    assert not (host.bin_dir / "hermes.argv").exists()


def test_health_ping_failure_is_not_swallowed(host: _Host, server: _FakeServer) -> None:
    """A rejected ping (wrong URL, 404) must fail the unit instead of vanishing behind `|| true`."""
    server.status = 404

    result = _run(_PING, host, server)

    assert result.returncode == 22
    assert "404" in result.stderr


def test_health_ping_without_a_url_fails_even_while_hermes_is_down(
    host: _Host, server: _FakeServer
) -> None:
    """A missing URL is a misconfiguration to surface, not something the skip path may hide."""
    host.set_hermes_active(False)

    result = _run(_PING, host, server, env={"HERMES_HEALTHCHECKS__PING_URL": ""})

    assert result.returncode == 1
    assert "HERMES_HEALTHCHECKS__PING_URL" in result.stderr
    assert server.requests == []


def test_health_ping_keeps_the_ping_url_out_of_the_curl_argv(
    host: _Host, server: _FakeServer
) -> None:
    """The ping URL embeds the check's secret key; argv would show it to every local user."""
    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert server.requests[0].path == _PING_PATH
    assert _PING_PATH not in host.curl_argv()


# --- hermes-notify-crash.sh (ExecStopPost=, #380) ------------------------------

_CRASH = "hermes-notify-crash.sh"
_SLACK_TOKEN = "xoxb-0000000000-FAKE-slack-token-for-tests"  # noqa: S105 - test-only  # gitleaks:allow
_SLACK_CHANNEL = "#hermes-reports"
_SLACK_OK = b'{"ok":true,"ts":"1.2"}'


def _crash_env(server: _FakeServer, **result: str) -> dict[str, str]:
    """Return the env systemd gives ExecStopPost= plus the Slack credentials."""
    return {
        "HERMES_SLACK__BOT_TOKEN": _SLACK_TOKEN,
        "HERMES_SLACK__DEFAULT_CHANNEL": _SLACK_CHANNEL,
        "HERMES_NOTIFY_SLACK_API_BASE": server.url,
        "HOSTNAME": "hermes-01",
        **result,
    }


def _slack_form(server: _FakeServer) -> dict[str, str]:
    """Return the single chat.postMessage form the server saw."""
    assert [(r.method, r.path) for r in server.requests] == [("POST", "/api/chat.postMessage")]
    return {key: values[0] for key, values in parse_qs(server.requests[0].body).items()}


def test_crash_notice_sends_nothing_after_a_clean_stop(host: _Host, server: _FakeServer) -> None:
    """A deploy or SIGTERM stop (SERVICE_RESULT=success) must stay quiet."""
    server.response = _SLACK_OK

    result = _run(
        _CRASH, host, server, "hermes.service", env=_crash_env(server, SERVICE_RESULT="success")
    )

    assert (result.returncode, server.requests) == (0, [])


def test_crash_notice_posts_one_slack_line_for_an_error_exit(
    host: _Host, server: _FakeServer
) -> None:
    """A crash that Restart= will recover from still reaches Slack, with unit, result and host."""
    server.response = _SLACK_OK
    env = _crash_env(server, SERVICE_RESULT="exit-code", EXIT_CODE="exited", EXIT_STATUS="1")

    result = _run(_CRASH, host, server, "hermes.service", env=env)

    form = _slack_form(server)
    assert result.returncode == 0, result.stderr
    assert form["channel"] == _SLACK_CHANNEL
    assert form["text"] == (
        "hermes.service on hermes-01 exited with an error"
        " (result=exit-code, code=exited, status=1)."
        " systemd restarts it in 30s; Telegram alerts if it stays down."
    )
    assert server.requests[0].authorization == f"Bearer {_SLACK_TOKEN}"


def test_crash_notice_names_the_signal_for_a_killed_process(
    host: _Host, server: _FakeServer
) -> None:
    """A segfault or OOM kill reports the signal, not just "failed"."""
    server.response = _SLACK_OK
    env = _crash_env(server, SERVICE_RESULT="signal", EXIT_CODE="killed", EXIT_STATUS="SEGV")

    _run(_CRASH, host, server, "hermes.service", env=env)

    assert "(result=signal, code=killed, status=SEGV)" in _slack_form(server)["text"]


def test_crash_notice_appends_the_last_serve_run_failed_line_redacted(
    host: _Host, server: _FakeServer
) -> None:
    """The newest serve.run.failed line goes along, with secret values masked."""
    server.response = _SLACK_OK
    host.set_journal(
        [
            "serve.run.failed old error",
            "serve.bar.processed BTC/USDT 4h",
            f"serve.run.failed LedgerError: disk full token={_SLACK_TOKEN}",
        ]
    )
    env = _crash_env(server, SERVICE_RESULT="exit-code", EXIT_CODE="exited", EXIT_STATUS="1")

    _run(_CRASH, host, server, "hermes.service", env=env)

    last = _slack_form(server)["text"].splitlines()[-1]
    assert last == "Last error: serve.run.failed LedgerError: disk full token=[redacted]"


def test_crash_notice_keeps_the_slack_token_out_of_the_curl_argv(
    host: _Host, server: _FakeServer
) -> None:
    """The bot token rides in a header from a stdin config, never in argv."""
    server.response = _SLACK_OK
    env = _crash_env(server, SERVICE_RESULT="exit-code", EXIT_CODE="exited", EXIT_STATUS="1")

    _run(_CRASH, host, server, "hermes.service", env=env)

    assert _SLACK_TOKEN not in host.curl_argv()


def test_crash_notice_reports_a_slack_refusal_but_still_exits_zero(
    host: _Host, server: _FakeServer
) -> None:
    """Slack's HTTP 200 + ok:false is an error to log; the stop result must not change."""
    server.response = b'{"ok":false,"error":"channel_not_found"}'
    env = _crash_env(server, SERVICE_RESULT="exit-code", EXIT_CODE="exited", EXIT_STATUS="1")

    result = _run(_CRASH, host, server, "hermes.service", env=env)

    assert result.returncode == 0
    assert "channel_not_found" in result.stderr


def test_crash_notice_without_credentials_logs_and_exits_zero(
    host: _Host, server: _FakeServer
) -> None:
    """Missing Slack settings are logged, sent nowhere, and never fail the stop."""
    env = _crash_env(server, SERVICE_RESULT="exit-code", HERMES_SLACK__BOT_TOKEN="")

    result = _run(_CRASH, host, server, "hermes.service", env=env)

    assert (result.returncode, server.requests) == (0, [])
    assert "HERMES_SLACK__BOT_TOKEN" in result.stderr


# integration: none feasible in CI (borg needs a Storage Box); the operator check is
# `systemctl start hermes-backup.service` on hermes-01 and the healthchecks.io check.
_BACKUP = "hermes-backup.sh"

_STUB_BORG = """#!/usr/bin/env bash
exit 0
"""

_APPENDING_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


def _run_backup(
    host: _Host, server: _FakeServer, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run the backup with a no-op borg, an empty data dir and every curl call recorded."""
    host._write_exe("borg", _STUB_BORG)
    host._write_exe("curl", _APPENDING_SPY_CURL)
    data = tmp_path / "data"
    data.mkdir()
    return _run(
        _BACKUP,
        host,
        server,
        env={
            "HERMES_BACKUP_BORG_REPO": "ssh://u0@example.invalid:23/./hermes",
            "BORG_PASSPHRASE": "fake-passphrase",  # gitleaks:allow
            "HERMES_BACKUP_HEALTHCHECK_URL": f"{server.url}{_PING_PATH}",
            "HERMES_DATA_DIR": data.as_posix(),
        },
    )


def test_backup_pings_start_then_success(host: _Host, server: _FakeServer, tmp_path: Path) -> None:
    """A good backup pings /start, then the bare URL, so healthchecks.io sees it finish."""
    result = _run_backup(host, server, tmp_path)

    assert result.returncode == 0, result.stderr
    assert [r.path for r in server.requests] == [f"{_PING_PATH}/start", _PING_PATH]


def test_backup_keeps_the_ping_url_out_of_curl_argv(
    host: _Host, server: _FakeServer, tmp_path: Path
) -> None:
    """The ping URL holds the check's secret, and `ps` shows argv to every local user (#782)."""
    _run_backup(host, server, tmp_path)

    argv = host.curl_argv()
    assert argv.count("--config") == 2
    assert _PING_PATH not in argv
