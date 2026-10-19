"""Behaviour of ``deploy/hermes-lab-health-ping.sh``, the staging lab's own liveness ping (#1079).

Invariants:

- every enabled shadow active and current: one success ping, whose body names
  each shadow with its ``hermes status`` output;
- any enabled shadow inactive, late or unreadable: one ``/fail`` ping naming it;
- no shadow enabled: no ping at all, so the lab's check alerts on silence;
- each shadow is checked on its own unit and its own ledger, from the staging
  release's config;
- a missing URL or a shadow name that is not plain fails the run with no ping;
- a rejected ping fails the run; the ping URL never reaches curl's argv.

The script runs on the lab box and nothing in CI starts it, so these tests run
the real script under bash with the real ``curl`` against a loopback HTTP
server. The stand-ins are ``systemctl`` (it needs a live systemd) and the
``hermes`` binary; the operator check is ``systemctl start
hermes-lab-health.service`` and the check's log (deploy/README.md).
"""

import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.integration.deploy._bash import run_bash
from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "deploy" / "hermes-lab-health-ping.sh"
_BASH = shutil.which("bash")
_CURL = shutil.which("curl")

pytestmark = pytest.mark.skipif(
    _BASH is None or _CURL is None, reason="the lab ping needs bash and curl"
)

_PING_PATH = "/ping/11111111-lab-fake-uuid"
_OK = '{"verdict": {"level": "ok"}}'
_LATE = '{"verdict": {"level": "late"}}'

_STUB_SYSTEMCTL = """#!/usr/bin/env bash
here="$(dirname "$0")"
if [ "$1 $2" != "is-active --quiet" ]; then
    echo "unexpected systemctl call: $*" >&2
    exit 99
fi
[ -e "$here/active.$3" ]
"""

_FAKE_HERMES = """#!/usr/bin/env bash
here="$(dirname "$0")"
printf '%s\\n' "$*" >> "$here/hermes.calls"
unit=""
while [ "$#" -gt 0 ]; do
    [ "$1" = "--unit" ] && unit="$2"
    shift
done
cat "$here/out.$unit"
exit "$(cat "$here/rc.$unit")"
"""

_SPY_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/curl.argv"
exec "$REAL_CURL" "$@"
"""


@dataclass
class _Ping:
    path: str
    body: str


@dataclass
class _FakeChecks:
    """Loopback healthchecks endpoint recording every ping."""

    url: str
    pings: list[_Ping] = field(default_factory=list)
    status: int = 200


@pytest.fixture
def checks() -> Iterator[_FakeChecks]:
    """Serve on a free loopback port; every ping is recorded on the fixture."""
    fake = _FakeChecks(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            fake.pings.append(_Ping(self.path, self.rfile.read(length).decode("utf-8")))
            self.send_response(fake.status)
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


class _Lab:
    """A scratch lab box: enabled-unit links, stand-in tools and per-shadow answers."""

    def __init__(self, root: Path) -> None:
        self.bin_dir = root / "bin"
        self.unit_dir = root / "systemd"
        self.wants = self.unit_dir / "multi-user.target.wants"
        self.bin_dir.mkdir()
        self.wants.mkdir(parents=True)
        for name, text in (
            ("systemctl", _STUB_SYSTEMCTL),
            ("hermes", _FAKE_HERMES),
            ("curl", _SPY_CURL),
        ):
            path = self.bin_dir / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)

    def shadow(self, name: str, *, active: bool = True, rc: int = 0, out: str = _OK) -> None:
        """Enable shadow ``name`` with the given unit state and status answer."""
        unit = f"hermes-shadow@{name}.service"
        (self.wants / unit).write_text("", encoding="utf-8")
        if active:
            (self.bin_dir / f"active.{unit}").write_text("", encoding="utf-8")
        (self.bin_dir / f"rc.{unit}").write_text(str(rc), encoding="utf-8")
        (self.bin_dir / f"out.{unit}").write_text(out, encoding="utf-8")

    def status_calls(self) -> list[str]:
        """Return each `hermes` argv the script ran, one string per call."""
        calls = self.bin_dir / "hermes.calls"
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def curl_argv(self) -> str:
        """Return the argv the script gave curl, one argument per line."""
        return (self.bin_dir / "curl.argv").read_text(encoding="utf-8")


@pytest.fixture
def lab(tmp_path: Path) -> _Lab:
    """Return a scratch lab with no shadow enabled yet."""
    return _Lab(tmp_path)


def _run(
    lab: _Lab, checks: _FakeChecks, url: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real script with the stand-ins first on PATH and the lab env file's URL set."""
    assert _CURL is not None
    environ = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("HERMES_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    }
    environ |= {
        "PATH": f"{lab.bin_dir}{os.pathsep}{environ['PATH']}",
        "REAL_CURL": _CURL,
        "NO_PROXY": "*",
        "HERMES_HEALTHCHECKS__PING_URL": f"{checks.url}{_PING_PATH}" if url is None else url,
        "HERMES_HEALTH_HERMES_BIN": (lab.bin_dir / "hermes").as_posix(),
        "HERMES_HEALTH_UNIT_DIR": lab.unit_dir.as_posix(),
    }
    return run_bash([_SCRIPT.as_posix()], environ)


def test_a_current_lab_pings_success_with_each_shadow_named(lab: _Lab, checks: _FakeChecks) -> None:
    """Both shadows active and current: one success ping carrying both status outputs."""
    lab.shadow("swing-a")
    lab.shadow("swing-b")

    result = _run(lab, checks)

    assert result.returncode == 0, result.stderr
    assert [(p.path, p.body) for p in checks.pings] == [
        (_PING_PATH, f"== swing-a\n{_OK}\n== swing-b\n{_OK}\n")
    ]


def test_a_late_shadow_turns_the_lab_ping_to_fail(lab: _Lab, checks: _FakeChecks) -> None:
    """One stalled shadow among current ones is enough to alert."""
    lab.shadow("swing-a")
    lab.shadow("swing-b", rc=1, out=_LATE)

    result = _run(lab, checks)

    assert result.returncode == 0, result.stderr
    assert [(p.path, p.body) for p in checks.pings] == [
        (f"{_PING_PATH}/fail", f"== swing-a\n{_OK}\n== swing-b\n{_LATE}\n")
    ]


def test_a_stopped_shadow_fails_the_ping_without_asking_its_status(
    lab: _Lab, checks: _FakeChecks
) -> None:
    """An enabled shadow that is not running is down, whatever its ledger says."""
    lab.shadow("swing-a", active=False)

    result = _run(lab, checks)

    assert result.returncode == 0, result.stderr
    assert (
        [(p.path, p.body) for p in checks.pings],
        lab.status_calls(),
    ) == ([(f"{_PING_PATH}/fail", "== swing-a\nhermes-shadow@swing-a.service is not active\n")], [])


def test_an_unreadable_shadow_ledger_fails_the_ping(lab: _Lab, checks: _FakeChecks) -> None:
    """Exit 3 (no ledger, or a config the lab refuses) is a fail, not a skip."""
    lab.shadow("swing-a", rc=3, out="ERROR: config load failed: prod value")

    _run(lab, checks)

    assert [p.path for p in checks.pings] == [f"{_PING_PATH}/fail"]


def test_a_lab_with_no_shadow_enabled_sends_no_ping(lab: _Lab, checks: _FakeChecks) -> None:
    """A switched-off lab goes silent, and the silence is what alerts the lab chat."""
    result = _run(lab, checks)

    assert (result.returncode, checks.pings, "ping skipped" in result.stdout) == (0, [], True)


def test_each_shadow_is_checked_on_its_own_unit_and_ledger(lab: _Lab, checks: _FakeChecks) -> None:
    """Status reads the staging config, the shadow's own instance and its own ledger."""
    lab.shadow("swing-a")

    _run(lab, checks)

    assert lab.status_calls() == [
        "status --config-dir /opt/hermes-staging/current/config "
        "--unit hermes-shadow@swing-a.service "
        "--db /var/lib/hermes-staging/shadows/swing-a/hermes.sqlite3 --json"
    ]


def test_a_missing_url_fails_without_pinging(lab: _Lab, checks: _FakeChecks) -> None:
    """No URL is a misconfiguration to surface, even with the lab off."""
    result = _run(lab, checks, url="")

    assert (result.returncode, checks.pings) == (1, [])
    assert "HERMES_HEALTHCHECKS__PING_URL" in result.stderr


def test_a_shadow_name_that_is_not_plain_fails_without_pinging(
    lab: _Lab, checks: _FakeChecks
) -> None:
    """A name becomes a ledger path, so anything but [a-z0-9-] is refused."""
    lab.shadow("Bad_Name")

    result = _run(lab, checks)

    assert (result.returncode, checks.pings, lab.status_calls()) == (1, [], [])


def test_a_rejected_ping_fails_the_run(lab: _Lab, checks: _FakeChecks) -> None:
    """A wrong URL (404) must fail the unit, not vanish."""
    lab.shadow("swing-a")
    checks.status = 404

    result = _run(lab, checks)

    assert result.returncode == 22


def test_the_ping_url_never_reaches_curls_argv(lab: _Lab, checks: _FakeChecks) -> None:
    """The URL embeds the check's key; argv would show it to every local user."""
    lab.shadow("swing-a")

    _run(lab, checks)

    assert (checks.pings[0].path, _PING_PATH in lab.curl_argv()) == (_PING_PATH, False)
