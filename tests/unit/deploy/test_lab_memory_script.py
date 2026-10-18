"""Behaviour of ``deploy/hermes-lab-memory.sh``, the staging lab's memory watch (issue #1151).

Invariants:

- one Telegram alert when ``MemAvailable`` drops below 1 GiB (1023 MiB alerts,
  1024 MiB does not), and none again until it recovers;
- one recovery message when it climbs back to 1 GiB or more;
- an alert that fails to send is retried on the next run, never dropped;
- every run logs the available memory, each running ``hermes-shadow@``
  instance's ``MemoryCurrent`` and their total, so a day of runs gives the
  figures ADR-024 §6 asks for;
- the bot token never reaches curl's argv.

The script runs on hermes-01 and nothing in CI starts it, so these tests run
the real script under bash with the real ``curl`` against a loopback HTTP
server. The only stand-in is ``systemctl`` (it needs a live systemd); the
operator check is ``systemctl start hermes-lab-memory.service`` and its
journal line (deploy/README.md).
"""

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

_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "hermes-lab-memory.sh"
_BASH = shutil.which("bash")
_CURL = shutil.which("curl")

pytestmark = pytest.mark.skipif(
    _BASH is None or _CURL is None, reason="the memory watch needs bash and curl"
)

# Obviously fake values shaped like the real ones.
_BOT_TOKEN = "123456789:FAKE-bot-token_for_tests"  # noqa: S105 - test-only token  # gitleaks:allow
_CHAT_ID = "-1001234567890"
_MIB = 1024 * 1024

_STUB_SYSTEMCTL = """#!/usr/bin/env bash
here="$(dirname "$0")"
case "$1" in
    list-units) cat "$here/units.txt" ;;
    show) cat "$here/mem.${@: -1}" 2> /dev/null || echo "[not set]" ;;
    *) echo "unexpected systemctl call: $*" >&2; exit 99 ;;
esac
"""

_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


@dataclass
class _FakeTelegram:
    """Loopback endpoint that records every request body and answers ``status``."""

    url: str
    bodies: list[str] = field(default_factory=list)
    status: int = 200


@pytest.fixture
def telegram() -> Iterator[_FakeTelegram]:
    """Serve on a free loopback port; every request body is recorded on the fixture."""
    fake = _FakeTelegram(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            fake.bodies.append(self.rfile.read(length).decode("utf-8"))
            self.send_response(fake.status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

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


class _Lab:
    """A scratch host: fake ``/proc/meminfo``, stand-in ``systemctl`` and the state directory."""

    def __init__(self, root: Path) -> None:
        self.bin_dir = root / "bin"
        self.state_dir = root / "state"
        self.meminfo = root / "meminfo"
        self.bin_dir.mkdir()
        self.state_dir.mkdir()
        for name, text in (("systemctl", _STUB_SYSTEMCTL), ("curl", _SPY_CURL)):
            path = self.bin_dir / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.set_shadows({})
        self.set_available_mib(2048)

    def set_available_mib(self, mib: int) -> None:
        """Write a meminfo whose ``MemAvailable`` is ``mib`` MiB."""
        self.meminfo.write_text(
            f"MemTotal:        3800000 kB\nMemFree:          200000 kB\n"
            f"MemAvailable:    {mib * 1024} kB\n",
            encoding="utf-8",
        )

    def set_shadows(self, shadows: dict[str, int]) -> None:
        """Make ``hermes-shadow@<name>.service`` run with ``bytes`` of ``MemoryCurrent`` each."""
        units = [f"hermes-shadow@{name}.service" for name in shadows]
        lines = "".join(f"{unit} loaded active running Hermes shadow\n" for unit in units)
        (self.bin_dir / "units.txt").write_text(lines, encoding="utf-8")
        for unit, size in zip(units, shadows.values(), strict=True):
            (self.bin_dir / f"mem.{unit}").write_text(f"{size}\n", encoding="utf-8")

    def run(self, telegram: _FakeTelegram) -> subprocess.CompletedProcess[str]:
        """Run the real script once with the stand-ins first on PATH."""
        assert _BASH is not None
        assert _CURL is not None
        environ = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("HERMES_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
        }
        environ.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{environ['PATH']}",
                "REAL_CURL": _CURL,
                "NO_PROXY": "*",
                "HERMES_TELEGRAM__BOT_TOKEN": _BOT_TOKEN,
                "HERMES_TELEGRAM__CHAT_ID": _CHAT_ID,
                "HERMES_NOTIFY_API_BASE": telegram.url,
                "HERMES_MEMINFO": self.meminfo.as_posix(),
                "STATE_DIRECTORY": self.state_dir.as_posix(),
                "HOSTNAME": "hermes-01",
            }
        )
        return subprocess.run(  # noqa: S603
            [_BASH, _SCRIPT.as_posix()],
            env=environ,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


@pytest.fixture
def lab(tmp_path: Path) -> _Lab:
    """Return a scratch host with 2048 MiB available and no shadow running."""
    return _Lab(tmp_path)


def _texts(telegram: _FakeTelegram) -> list[str]:
    return [parse_qs(body)["text"][0].splitlines()[0] for body in telegram.bodies]


def test_1023_mib_available_sends_one_low_memory_alert(lab: _Lab, telegram: _FakeTelegram) -> None:
    """One MiB under the 1 GiB floor alerts once, naming the host and the figure."""
    lab.set_available_mib(1023)

    result = lab.run(telegram)

    assert (result.returncode, _texts(telegram)) == (
        0,
        ["Lab memory low on hermes-01: 1023 MiB available, under 1024 MiB."],
    ), result.stderr


def test_1024_mib_available_sends_nothing(lab: _Lab, telegram: _FakeTelegram) -> None:
    """Exactly 1 GiB available is not below the floor: no message."""
    lab.set_available_mib(1024)

    result = lab.run(telegram)

    assert (result.returncode, telegram.bodies) == (0, []), result.stderr


def test_a_long_low_spell_alerts_only_once(lab: _Lab, telegram: _FakeTelegram) -> None:
    """Three runs under the floor send one alert, not one a minute."""
    lab.set_available_mib(900)

    codes = [lab.run(telegram).returncode for _ in range(3)]

    assert (codes, len(telegram.bodies)) == ([0, 0, 0], 1)


def test_recovery_sends_one_message_and_rearms_the_alert(
    lab: _Lab, telegram: _FakeTelegram
) -> None:
    """Low, recovered, recovered, low again: alert, one recovery, then a fresh alert."""
    for mib in (900, 1500, 1500, 950):
        lab.set_available_mib(mib)
        lab.run(telegram)

    assert _texts(telegram) == [
        "Lab memory low on hermes-01: 900 MiB available, under 1024 MiB.",
        "Lab memory recovered on hermes-01: 1500 MiB available.",
        "Lab memory low on hermes-01: 950 MiB available, under 1024 MiB.",
    ]


def test_an_alert_that_fails_to_send_is_retried_on_the_next_run(
    lab: _Lab, telegram: _FakeTelegram
) -> None:
    """Telegram answering 500 leaves the alert unsent, so the next run sends it again."""
    lab.set_available_mib(900)
    telegram.status = 500
    failed = lab.run(telegram)
    telegram.status = 200

    retried = lab.run(telegram)

    assert (failed.returncode != 0, retried.returncode, len(telegram.bodies)) == (True, 0, 4)


def test_every_run_logs_each_shadow_and_the_total(lab: _Lab, telegram: _FakeTelegram) -> None:
    """Two shadows at 80 and 95 MiB log one line: available, total 175 MiB, then each instance."""
    lab.set_shadows({"swing-donchian-4h": 80 * _MIB, "swing-b1-daily-ma": 95 * _MIB})

    result = lab.run(telegram)

    assert result.stdout.splitlines() == [
        "hermes-lab-memory: available=2048MiB shadows_total=175MiB "
        "swing-donchian-4h=80MiB swing-b1-daily-ma=95MiB"
    ]


def test_a_shadow_without_memory_accounting_is_left_out_of_the_total(
    lab: _Lab, telegram: _FakeTelegram
) -> None:
    """The all-ones value systemd prints with no accounting is not counted as memory in use."""
    lab.set_shadows({"a": 10 * _MIB, "b": 18446744073709551615})

    result = lab.run(telegram)

    assert result.stdout.splitlines() == [
        "hermes-lab-memory: available=2048MiB shadows_total=10MiB a=10MiB"
    ]


def test_an_unreadable_meminfo_fails_without_sending(lab: _Lab, telegram: _FakeTelegram) -> None:
    """No ``MemAvailable`` line means nothing was measured: exit 1, no message."""
    lab.meminfo.write_text("MemTotal: 3800000 kB\n", encoding="utf-8")

    result = lab.run(telegram)

    assert (result.returncode, telegram.bodies) == (1, [])


def test_the_bot_token_never_reaches_curl_argv(lab: _Lab, telegram: _FakeTelegram) -> None:
    """The token travels in curl's config on stdin, out of ``ps`` for every local user."""
    lab.set_available_mib(900)
    lab.run(telegram)

    argv = (lab.bin_dir / "curl.argv").read_text(encoding="utf-8")

    assert (_BOT_TOKEN in argv, "--config" in argv) == (False, True)
