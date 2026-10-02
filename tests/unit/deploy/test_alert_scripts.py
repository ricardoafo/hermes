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

_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


@dataclass
class _Received:
    method: str
    path: str
    body: str


@dataclass
class _FakeServer:
    """Loopback HTTP endpoint that records every request and answers ``status``."""

    url: str
    requests: list[_Received] = field(default_factory=list)
    status: int = 200


@pytest.fixture
def server() -> Iterator[_FakeServer]:
    """Serve on a free loopback port; every request is recorded on the fixture."""
    fake = _FakeServer(url="")

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            fake.requests.append(_Received(self.command, self.path, body))
            self.send_response(fake.status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = _record  # noqa: N815
        do_POST = _record  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            """Keep pytest output quiet."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
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
        self.set_hermes_active(True)
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


def test_health_ping_is_sent_while_hermes_is_active(host: _Host, server: _FakeServer) -> None:
    """The dead-man's switch is fed exactly once per tick while hermes.service is active."""
    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert [(r.method, r.path) for r in server.requests] == [("GET", _PING_PATH)]


def test_health_ping_is_skipped_while_hermes_is_not_active(
    host: _Host, server: _FakeServer
) -> None:
    """No ping while the service is down: the missed ping is what alerts the operator."""
    host.set_hermes_active(False)

    result = _run(_PING, host, server)

    assert result.returncode == 0, result.stderr
    assert server.requests == []
    assert "ping skipped" in result.stdout


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
