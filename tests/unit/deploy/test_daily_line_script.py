"""Behaviour of ``deploy/hermes-daily-line.sh`` (the daily alive line, #373).

Runs the real script under bash with the real curl against a loopback fake
Slack. Stand-ins: ``systemctl`` (answers ``show ... NRestarts``),
``journalctl`` (prints canned lines) and a fake ``hermes`` that records its
argv and stdin and prints a canned line. The summary's own wording is tested
in ``tests/unit/entrypoints/test_daily_line.py``.

Invariants protected here:

- one Slack post per run with the line `hermes status --summary` printed;
- the journal reaches `hermes` on stdin, with `--journal -`;
- restarts are the NRestarts difference since the previous run, kept in the
  state directory, and a counter that went down counts from zero;
- the bot token never reaches curl's argv;
- missing credentials and Slack's ``ok:false`` fail the run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "hermes-daily-line.sh"
_BASH = shutil.which("bash")
_CURL = shutil.which("curl")

pytestmark = pytest.mark.skipif(
    _BASH is None or _CURL is None, reason="the daily line script needs bash and curl"
)

_TOKEN = "xoxb-0000000000-FAKE-slack-token-for-tests"  # noqa: S105 - test-only  # gitleaks:allow
_CHANNEL = "#hermes-reports"
_LINE = "hermes alive · 18x4h + 3x1d bars in 24 h · 0 signals · 0 errors · 0 warnings · 0 restarts"

_STUB_SYSTEMCTL = """#!/usr/bin/env bash
cat "$(dirname "$0")/nrestarts"
"""
_STUB_JOURNALCTL = """#!/usr/bin/env bash
cat "$(dirname "$0")/journal.txt"
"""
_FAKE_HERMES = """#!/usr/bin/env bash
here="$(dirname "$0")"
printf '%s\\n' "$@" > "$here/hermes.argv"
cat > "$here/hermes.stdin"
cat "$here/hermes.out"
exit "$(cat "$here/hermes.rc")"
"""
_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


@dataclass
class _Slack:
    url: str
    forms: list[dict[str, str]] = field(default_factory=list)
    authorizations: list[str] = field(default_factory=list)
    pings: list[str] = field(default_factory=list)
    response: bytes = b'{"ok":true}'


@pytest.fixture
def slack() -> Iterator[_Slack]:
    """Loopback fake of chat.postMessage."""
    fake = _Slack(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            fake.forms.append({k: v[0] for k, v in parse_qs(body).items()})
            fake.authorizations.append(self.headers.get("Authorization") or "")
            self.send_response(200)
            self.send_header("Content-Length", str(len(fake.response)))
            self.end_headers()
            self.wfile.write(fake.response)

        def do_GET(self) -> None:
            fake.pings.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format: str, *args: object) -> None:
            """Keep pytest output quiet."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


class _Host:
    """Scratch PATH with the stand-ins and a state directory."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.bin.mkdir()
        self.state = root / "state"
        for name, text in (
            ("systemctl", _STUB_SYSTEMCTL),
            ("journalctl", _STUB_JOURNALCTL),
            ("hermes", _FAKE_HERMES),
            ("curl", _SPY_CURL),
        ):
            path = self.bin / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.set("nrestarts", "0")
        self.set("journal.txt", '{"event": "serve.bar.processed", "level": "info"}\n')
        self.set("hermes.out", _LINE + "\n")
        self.set("hermes.rc", "0")

    def set(self, name: str, text: str) -> None:
        """Write a stand-in's canned answer."""
        (self.bin / name).write_text(text, encoding="utf-8", newline="\n")

    def read(self, name: str) -> str:
        """Read what a stand-in recorded."""
        return (self.bin / name).read_text(encoding="utf-8")


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    """Return the scratch host."""
    return _Host(tmp_path)


def _run(host: _Host, slack: _Slack, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the script with the stand-ins first on PATH."""
    assert _BASH is not None
    assert _CURL is not None
    environ = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("HERMES_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    }
    environ.update(
        {
            "PATH": f"{host.bin}{os.pathsep}{environ['PATH']}",
            "REAL_CURL": _CURL,
            "NO_PROXY": "*",
            "HERMES_DAILY_HERMES_BIN": (host.bin / "hermes").as_posix(),
            "HERMES_SLACK__API_BASE": slack.url,
            "HERMES_SLACK__BOT_TOKEN": _TOKEN,
            "HERMES_SLACK__DEFAULT_CHANNEL": _CHANNEL,
            "STATE_DIRECTORY": host.state.as_posix(),
        }
    )
    environ.update(env)
    return subprocess.run(  # noqa: S603
        [_BASH, _SCRIPT.as_posix()],
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_sends_the_summary_line_to_the_default_channel(host: _Host, slack: _Slack) -> None:
    """One post per run, carrying exactly what `hermes status --summary` printed."""
    result = _run(host, slack)

    assert result.returncode == 0, result.stderr
    assert slack.forms == [{"channel": _CHANNEL, "text": _LINE}]
    assert slack.authorizations == [f"Bearer {_TOKEN}"]


def test_a_dedicated_channel_overrides_the_default(host: _Host, slack: _Slack) -> None:
    """HERMES_DAILY_LINE__CHANNEL routes the line elsewhere."""
    _run(host, slack, HERMES_DAILY_LINE__CHANNEL="#hermes-daily")

    assert [form["channel"] for form in slack.forms] == ["#hermes-daily"]


def test_hermes_gets_the_journal_on_stdin_and_the_summary_flags(host: _Host, slack: _Slack) -> None:
    """The journal goes to `hermes status --summary --journal -` with the restart count."""
    _run(host, slack)

    assert host.read("hermes.argv").splitlines() == [
        "status",
        "--config-dir",
        "/opt/hermes/current/config",
        "--db",
        "/var/lib/hermes/hermes.sqlite3",
        "--summary",
        "--journal",
        "-",
        "--restarts",
        "0",
    ]
    assert host.read("hermes.stdin") == '{"event": "serve.bar.processed", "level": "info"}\n'


def test_restarts_are_the_difference_since_the_previous_run(host: _Host, slack: _Slack) -> None:
    """NRestarts is cumulative; the line reports today's increase only."""
    host.set("nrestarts", "3")
    _run(host, slack)
    host.set("nrestarts", "5")

    _run(host, slack)

    assert host.read("hermes.argv").splitlines()[-1] == "2"
    assert (host.state / "nrestarts").read_text(encoding="utf-8") == "5\n"


def test_a_counter_that_went_down_counts_from_zero(host: _Host, slack: _Slack) -> None:
    """After a reboot NRestarts restarts at 0; a negative difference is meaningless."""
    host.state.mkdir()
    (host.state / "nrestarts").write_text("7\n", encoding="utf-8")
    host.set("nrestarts", "1")

    _run(host, slack)

    assert host.read("hermes.argv").splitlines()[-1] == "1"


def test_a_late_or_down_line_is_still_sent(host: _Host, slack: _Slack) -> None:
    """`hermes status` exits 1 on a late day; that line matters most and must go out."""
    host.set("hermes.out", "hermes LATE: The 22:00 check is 29 minutes late · ...\n")
    host.set("hermes.rc", "1")

    result = _run(host, slack)

    assert result.returncode == 0, result.stderr
    assert [form["text"] for form in slack.forms] == [
        "hermes LATE: The 22:00 check is 29 minutes late · ..."
    ]


def test_the_bot_token_stays_out_of_the_curl_argv(host: _Host, slack: _Slack) -> None:
    """The token rides in a header from a stdin config, never in argv."""
    _run(host, slack)

    assert _TOKEN not in host.read("curl.argv")


def test_missing_credentials_fail_without_sending(host: _Host, slack: _Slack) -> None:
    """No token: exit 1 and nothing posted, so the failed unit shows in systemctl."""
    result = _run(host, slack, HERMES_SLACK__BOT_TOKEN="")

    assert (result.returncode, slack.forms) == (1, [])
    assert "HERMES_SLACK__BOT_TOKEN" in result.stderr


def test_a_slack_refusal_fails_the_run(host: _Host, slack: _Slack) -> None:
    """Slack's HTTP 200 with ok:false is a failure, not a success."""
    slack.response = b'{"ok":false,"error":"not_in_channel"}'

    result = _run(host, slack)

    assert result.returncode == 2
    assert "not_in_channel" in result.stderr


_HC_PATH = "/hc/0f5c-check"


def test_a_sent_line_pings_the_dead_mans_switch(host: _Host, slack: _Slack) -> None:
    """#38: with a ping URL set, a sent line pings it once, without the id in curl's argv."""
    result = _run(host, slack, HERMES_HEALTHCHECKS__DAILY_PING_URL=slack.url + _HC_PATH)

    assert (result.returncode, slack.pings) == (0, [_HC_PATH])
    assert _HC_PATH not in host.read("curl.argv")


def test_a_refused_line_pings_fail(host: _Host, slack: _Slack) -> None:
    """#38: a line Slack refuses pings <url>/fail and still exits 2."""
    slack.response = b'{"ok":false,"error":"not_in_channel"}'

    result = _run(host, slack, HERMES_HEALTHCHECKS__DAILY_PING_URL=slack.url + _HC_PATH)

    assert (result.returncode, slack.pings) == (2, [_HC_PATH + "/fail"])


def test_no_ping_url_means_no_ping(host: _Host, slack: _Slack) -> None:
    """Unset, the dead-man's switch is off and nothing is requested."""
    result = _run(host, slack)

    assert (result.returncode, slack.pings, len(slack.forms)) == (0, [], 1)
