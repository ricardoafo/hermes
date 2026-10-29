"""Every notifier endpoint reaches a loopback fake purely by config (#970).

Invariant: setting ``HERMES_TELEGRAM__API_BASE`` or ``HERMES_SLACK__API_BASE``
is all it takes to aim the production wiring at another host. The adapters
are built exactly as the entrypoints build them (``from_env`` then
``create``; ``hermes bot`` through its own ``main``), with no client
injected, and a real HTTP server on 127.0.0.1 records what arrives.
"""

import io
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hermes.adapters.notifier import SlackAdapter, SlackConfig, TelegramAdapter, TelegramConfig
from hermes.entrypoints import bot as bot_module
from tests.support.paths import REPO_ROOT

_TOKEN = "123456789:FAKE-bot-token_for_tests"  # noqa: S105 - test-only  # gitleaks:allow
_SLACK_TOKEN = "xoxb-fake-token-for-tests"  # noqa: S105 - test-only  # gitleaks:allow
_CHAT = "-1001234567890"
_REPO_CONFIG = REPO_ROOT / "config"


@dataclass
class _Fake:
    """A loopback Bot/Web API stand-in that records each request path."""

    url: str = ""
    paths: list[str] = field(default_factory=list)


@pytest.fixture
def fake() -> Iterator[_Fake]:
    """Serve ``{"ok": true, ...}`` on a free loopback port."""
    recorded = _Fake()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            recorded.paths.append(self.path.split("?", 1)[0])
            body = json.dumps({"ok": True, "result": [], "ts": "1.2"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Keep pytest output quiet."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    recorded.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield recorded
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_the_telegram_notifier_reaches_the_fake_by_config_alone(fake: _Fake) -> None:
    """An urgent push goes to the configured base, not api.telegram.org."""
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN,
        "HERMES_TELEGRAM__CHAT_ID": _CHAT,
        "HERMES_TELEGRAM__API_BASE": fake.url,
    }
    adapter = TelegramAdapter.create(TelegramConfig.from_env(env))
    try:
        adapter.notify_urgent(event="fake_check", correlation_id="01ABCDEF", message="hello")
    finally:
        adapter.close()

    assert fake.paths == [f"/bot{_TOKEN}/sendMessage"]


def test_the_slack_notifier_reaches_the_fake_by_config_alone(fake: _Fake) -> None:
    """A Slack post goes to the configured base, not slack.com."""
    env = {
        "HERMES_SLACK__BOT_TOKEN": _SLACK_TOKEN,
        "HERMES_SLACK__DEFAULT_CHANNEL": "#lab",
        "HERMES_SLACK__API_BASE": fake.url,
    }
    adapter = SlackAdapter.create(SlackConfig.from_env(env))
    try:
        adapter.notify_urgent(event="fake_check", correlation_id="01ABCDEF", message="hello")
    finally:
        adapter.close()

    assert fake.paths == ["/api/chat.postMessage"]


def test_hermes_bot_reaches_the_fake_by_config_alone(fake: _Fake, tmp_path: Path) -> None:
    """``hermes bot --once`` polls and answers through the configured base."""
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": _TOKEN,
        "HERMES_TELEGRAM__CHAT_ID": _CHAT,
        "HERMES_TELEGRAM__API_BASE": fake.url,
    }

    code = bot_module.main(
        ["--once", "--config-dir", str(_REPO_CONFIG), "--db", str(tmp_path / "absent.sqlite3")],
        stream=io.StringIO(),
        env=env,
        err=io.StringIO(),
    )

    assert (code, fake.paths) == (0, [f"/bot{_TOKEN}/getUpdates", f"/bot{_TOKEN}/sendMessage"])
