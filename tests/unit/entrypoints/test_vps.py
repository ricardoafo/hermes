"""``hermes --vps``: the ssh argv, the console-only refusal and the failure messages.

Nothing here connects anywhere: the ssh runner is a fake that records the argv.
Contract-seam note: the real ``subprocess.run`` seam is exercised only by the
operator's manual run against the VPS; no integration test may open a connection.
"""

from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import vps as vps_module
from hermes.entrypoints.vps import SshResult, VpsSettings, build_ssh_argv, run_vps

_CONSOLE = frozenset({"positions", "status", "tui"})
_SCRIPT_TAIL = (
    "set -a; . /etc/hermes/env.production; set +a; "
    'export HERMES_DB_PATH="${HERMES_DB_PATH:-/var/lib/hermes/hermes.sqlite3}" '
    'HERMES_CONFIG_DIR="${HERMES_CONFIG_DIR:-/opt/hermes/current/config}" '
    "HERMES_CONSOLE_REMOTE=1; "
    'cd /var/lib/hermes && exec runuser -u hermes -- "$@"'
)
_BIN = "/opt/hermes/current/.venv/bin/hermes"


@pytest.fixture(autouse=True)
def _no_real_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: no test in this file can open a connection, even through the CLI."""

    def boom(argv: list[str]) -> SshResult:
        raise AssertionError(f"a test reached the real ssh: {argv}")

    defaults = vps_module.run_vps.__kwdefaults__
    assert defaults is not None
    monkeypatch.setitem(defaults, "runner", boom)


class _FakeSsh:
    def __init__(self, result: SshResult | Exception) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> SshResult:
        self.calls.append(argv)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _key(tmp_path: Path) -> Path:
    key = tmp_path / "id_test"
    key.write_text("not a real key", encoding="utf-8")
    return key


def _run(
    tmp_path: Path,
    result: SshResult | Exception,
    command: str = "positions",
    args: list[str] | None = None,
) -> tuple[int, str, _FakeSsh]:
    fake = _FakeSsh(result)
    out = io.StringIO()
    code = run_vps(
        command,
        args or [],
        out,
        console_commands=_CONSOLE,
        env={"HERMES_VPS_KEY": str(_key(tmp_path))},
        runner=fake,
    )
    return code, out.getvalue(), fake


def test_default_settings_are_the_documented_operator_connection() -> None:
    """Invariant: with no env the target is root@hermes-01 and ~/.ssh/hermes_vps_ed25519."""
    home = Path("home")
    settings = VpsSettings.from_env({}, home=home)
    assert (settings.host, settings.user) == ("hermes-01", "root")
    assert settings.key == home / ".ssh" / "hermes_vps_ed25519"


def test_env_overrides_each_setting_and_blank_counts_as_unset() -> None:
    """Invariant: HERMES_VPS_* override the defaults one by one; an empty value does not."""
    env = {"HERMES_VPS_HOST": "box", "HERMES_VPS_USER": "ops", "HERMES_VPS_KEY": "k.pem"}
    assert VpsSettings.from_env(env) == VpsSettings("box", "ops", Path("k.pem"))
    blank = VpsSettings.from_env({"HERMES_VPS_HOST": "  "}, home=Path("h"))
    assert blank.host == "hermes-01"


def test_argv_for_defaults_is_exact() -> None:
    """Invariant: the ssh argv is a fixed list, remote side one shlex-quoted string."""
    settings = VpsSettings("hermes-01", "root", Path("k"))
    argv = build_ssh_argv(settings, "positions", ["BTC", "--utc"])
    remote = "sudo " + shlex.join(
        [
            "bash",
            "-c",
            vps_module.REMOTE_GUARD + _SCRIPT_TAIL,
            "_",
            _BIN,
            "positions",
            "BTC",
            "--utc",
        ]
    )
    assert argv == [
        "ssh",
        "-t",
        "-i",
        "k",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        "root@hermes-01",
        remote,
    ]


def test_argv_for_overrides_uses_the_given_host_user_and_key() -> None:
    """Invariant: overrides change only the identity part of the argv."""
    argv = build_ssh_argv(VpsSettings("box", "ops", Path("a b/k")), "status", [])
    assert argv[:4] == ["ssh", "-t", "-i", str(Path("a b/k"))]
    assert argv[-2] == "ops@box"
    assert argv[-1].endswith(f"{_BIN} status")


@given(st.lists(st.text(min_size=1, max_size=12), min_size=1, max_size=5))
def test_remote_args_survive_shell_quoting_round_trip(args: list[str]) -> None:
    """Invariant: whatever the operator types arrives as separate, intact remote args."""
    remote = build_ssh_argv(VpsSettings("h", "u", Path("k")), "logs", args)[-1]
    tokens = shlex.split(remote)
    assert tokens[:3] == ["sudo", "bash", "-c"]
    assert tokens[-len(args) - 2 :] == [_BIN, "logs", *args]


def test_hostile_arguments_stay_inside_one_remote_word(tmp_path: Path) -> None:
    """Invariant: shell metacharacters in args are quoted, never interpreted remotely."""
    code, _, fake = _run(tmp_path, SshResult(0, ""), args=["BTC; rm -rf /", "$(id)"])
    assert code == 0
    tokens = shlex.split(fake.calls[0][-1])
    assert tokens[-2:] == ["BTC; rm -rf /", "$(id)"]


@pytest.mark.parametrize("command", ["serve", "migrate", "supervisor"])
def test_non_console_commands_are_refused_without_running_ssh(tmp_path: Path, command: str) -> None:
    """Invariant: --vps is read-only; a writer command never reaches ssh (exit 2)."""
    code, out, fake = _run(tmp_path, SshResult(0, ""), command=command)
    assert (code, fake.calls) == (2, [])
    assert f"not {command!r}" in out
    assert "positions, status, tui" in out


def test_exit_code_of_the_remote_command_is_returned_without_replaying_stderr(
    tmp_path: Path,
) -> None:
    """Invariant: the remote exit code is the local one; stderr was already shown live."""
    code, out, _ = _run(tmp_path, SshResult(1, "Connection to hermes-01 closed.\n"))
    assert (code, out) == (1, "")


def test_ssh_binary_missing_maps_to_install_hint(tmp_path: Path) -> None:
    """Invariant: no OpenSSH client gives a one-line install hint, exit 2."""
    code, out, _ = _run(tmp_path, FileNotFoundError("ssh"))
    assert (code, out) == (2, "ERROR: ssh not found; install the OpenSSH client.\n")


@pytest.mark.parametrize(
    "stderr",
    [
        "ssh: Could not resolve hostname hermes-01: No such host is known.",
        "ssh: connect to host hermes-01 port 22: Connection timed out",
        "ssh: connect to host hermes-01 port 22: Connection refused",
        "ssh: connect to host hermes-01 port 22: Network is unreachable",
    ],
)
def test_unreachable_host_maps_to_tailscale_hint(tmp_path: Path, stderr: str) -> None:
    """Invariant: exit 255 with a connection message tells the operator to check Tailscale."""
    code, out, _ = _run(tmp_path, SshResult(255, stderr))
    assert code == 255
    assert (
        out == "ERROR: cannot reach hermes-01; is Tailscale connected? (or set HERMES_VPS_HOST).\n"
    )


def test_auth_failure_maps_to_key_hint(tmp_path: Path) -> None:
    """Invariant: Permission denied names the user, host and key to check."""
    code, out, _ = _run(tmp_path, SshResult(255, "root@hermes-01: Permission denied (publickey)."))
    assert code == 255
    assert out.startswith("ERROR: ssh authentication to root@hermes-01 failed; check the key (")
    assert out.count("\n") == 1


def test_unknown_ssh_failure_keeps_the_last_diagnostic_line(tmp_path: Path) -> None:
    """Invariant: an unmapped 255 still surfaces ssh's own last line, on one line."""
    code, out, _ = _run(tmp_path, SshResult(255, "warn\nHost key verification failed.\n"))
    assert (code, out) == (
        255,
        "ERROR: ssh to root@hermes-01 failed (exit 255): Host key verification failed.\n",
    )


def test_unknown_ssh_failure_without_stderr_has_no_dangling_colon(tmp_path: Path) -> None:
    """Invariant: the generic message is well formed with empty diagnostics."""
    _, out, _ = _run(tmp_path, SshResult(255, ""))
    assert out == "ERROR: ssh to root@hermes-01 failed (exit 255).\n"


def test_remote_binary_missing_maps_to_deploy_hint(tmp_path: Path) -> None:
    """Invariant: exit 127 means the VPS has no console; the message says deploy."""
    code, out, _ = _run(tmp_path, SshResult(127, ""))
    assert code == 127
    assert out == (
        f"ERROR: hermes is not at {_BIN} on the VPS; the VPS needs a deploy with the console.\n"
    )


def test_missing_key_file_is_refused_before_ssh(tmp_path: Path) -> None:
    """Invariant: a wrong key path is a clear local error, not an ssh password prompt."""
    fake = _FakeSsh(SshResult(0, ""))
    out = io.StringIO()
    code = run_vps(
        "positions",
        [],
        out,
        console_commands=_CONSOLE,
        env={"HERMES_VPS_KEY": str(tmp_path / "nope")},
        runner=fake,
    )
    assert (code, fake.calls) == (2, [])
    assert "ssh key not found" in out.getvalue()


@pytest.mark.parametrize(
    ("name", "value"), [("HERMES_VPS_HOST", "-oProxyCommand=x"), ("HERMES_VPS_USER", "a b")]
)
def test_unsafe_host_or_user_is_refused(tmp_path: Path, name: str, value: str) -> None:
    """Invariant: a host or user ssh could read as an option or split is refused."""
    fake = _FakeSsh(SshResult(0, ""))
    out = io.StringIO()
    code = run_vps(
        "positions",
        [],
        out,
        console_commands=_CONSOLE,
        env={name: value, "HERMES_VPS_KEY": str(_key(tmp_path))},
        runner=fake,
    )
    assert (code, fake.calls) == (2, [])


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--vps", "status"], (True, ["status"])),
        (["--vps"], (True, [])),
        (["status", "--vps"], (False, ["status", "--vps"])),
        ([], (False, [])),
    ],
)
def test_split_vps_flag_only_reads_the_leading_argument(
    args: list[str], expected: tuple[bool, list[str]]
) -> None:
    """Invariant: --vps is a leading global option; later it belongs to the command."""
    assert vps_module.split_vps_flag(args) == expected


def _cli(args: list[str]) -> tuple[int, str]:
    """Run the CLI and return the exit code and what it wrote to stderr; stdout must stay empty."""
    out, err = io.StringIO(), io.StringIO()
    code = cli_module.main(args, stream=out, err=err)
    assert out.getvalue() == ""
    return code, err.getvalue()


def test_cli_refuses_vps_for_serve() -> None:
    """Invariant: `hermes --vps serve` exits 2 through the real registry; nothing runs."""
    code, out = _cli(["--vps", "serve"])
    assert code == 2
    assert "only runs console commands" in out
    assert "not 'serve'" in out


def test_cli_vps_without_command_is_usage_error() -> None:
    """Invariant: `hermes --vps` alone explains it needs a console command."""
    code, out = _cli(["--vps"])
    assert (code, "needs a console command" in out) == (2, True)


@pytest.mark.parametrize(
    "args",
    [
        ["--vps", "--demo", "calm", "positions"],
        ["--vps", "--demo=calm", "positions"],
        ["--demo", "calm", "--vps", "positions"],
    ],
)
def test_cli_vps_with_demo_is_refused_in_either_order(args: list[str]) -> None:
    """Decision D-3: the demo is local fixture data, so combining it with --vps is refused."""
    code, out = _cli(args)
    assert code == 2
    assert "cannot be combined with --demo" in out


def test_cli_runs_a_console_command_through_the_vps_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: `hermes --vps positions BTC` forwards command and args to run_vps."""
    seen: dict[str, object] = {}

    def fake_run(command: str, args: list[str], stream: object, **kwargs: object) -> int:
        seen.update(command=command, args=args, commands=kwargs["console_commands"])
        return 7

    monkeypatch.setattr(vps_module, "run_vps", fake_run)
    code, _ = _cli(["--vps", "positions", "BTC"])
    assert code == 7
    assert seen["command"] == "positions"
    assert seen["args"] == ["BTC"]
    commands = seen["commands"]
    assert isinstance(commands, frozenset)
    assert "positions" in commands
    assert "serve" not in commands


def test_tee_forwards_a_prompt_without_newline_before_input_is_awaited() -> None:
    """Invariant: ssh's passphrase prompt (no trailing newline) reaches the user's stderr."""
    prompt = "Enter passphrase for key 'C:/k': "
    sink = io.StringIO()
    assert vps_module._tee(io.StringIO(prompt), sink) == prompt
    assert sink.getvalue() == prompt


def test_subprocess_runner_streams_stderr_live_and_returns_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invariant: the real runner echoes ssh's stderr (prompt visible) and keeps it for mapping.

    A python child stands in for ssh; nothing connects anywhere.
    """
    child = "import sys; sys.stderr.write('Enter passphrase for key k: '); sys.exit(3)"
    result = vps_module._subprocess_runner([sys.executable, "-c", child])
    assert result == SshResult(3, "Enter passphrase for key k: ")
    assert capsys.readouterr().err == "Enter passphrase for key k: "


def test_passphrase_prompt_adds_an_ssh_add_hint_only_then(tmp_path: Path) -> None:
    """Invariant: the ssh-agent hint appears after a passphrase prompt and never otherwise."""
    _, out, _ = _run(tmp_path, SshResult(0, "Enter passphrase for key 'k': "))
    assert out.startswith("hint: to stop typing the passphrase, run `ssh-add ")
    assert "Start-Service ssh-agent" in out
    _, quiet, _ = _run(tmp_path, SshResult(0, ""))
    assert quiet == ""


_BASH = os.environ.get("HERMES_TEST_BASH") or shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="needs bash")
@pytest.mark.parametrize(
    ("help_text", "expected_code"),
    [("  status  Health\n  tui  Home\n", 0), ("  status  Health\n", 127)],
)
def test_remote_guard_exits_127_when_the_vps_build_lacks_the_command(
    tmp_path: Path, help_text: str, expected_code: int
) -> None:
    """Invariant: a VPS build whose --help lacks the command (unknown subcommand) exits 127."""
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\nprintf '%s' '{help_text}'\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    assert _BASH is not None
    script = vps_module.REMOTE_GUARD + "true"
    done = subprocess.run(  # noqa: S603
        [_BASH, "-c", script, "_", fake.as_posix(), "tui"], capture_output=True, check=False
    )
    assert done.returncode == expected_code


@pytest.mark.parametrize(
    ("args", "expected_args"),
    [
        (["positions", "--vps"], []),
        (["positions", "BTC", "--vps", "--utc"], ["BTC", "--utc"]),
    ],
)
def test_cli_accepts_vps_after_the_command(
    monkeypatch: pytest.MonkeyPatch, args: list[str], expected_args: list[str]
) -> None:
    """Invariant: `hermes CMD --vps` runs CMD on the VPS, with --vps removed from its args."""
    seen: dict[str, object] = {}

    def fake_run(command: str, rest: list[str], stream: object, **kwargs: object) -> int:
        seen.update(command=command, args=rest)
        return 0

    monkeypatch.setattr(vps_module, "run_vps", fake_run)
    code, _ = _cli(args)
    assert code == 0
    assert seen == {"command": "positions", "args": expected_args}


def test_cli_refuses_vps_before_and_after_the_command() -> None:
    """Invariant: two --vps options are ambiguous, so exit 2 and nothing runs."""
    code, out = _cli(["--vps", "positions", "--vps"])
    assert (code, "give --vps once" in out) == (2, True)


@pytest.mark.parametrize(
    "args", [["positions", "--demo", "calm", "--vps"], ["--demo", "calm", "positions", "--vps"]]
)
def test_cli_refuses_trailing_vps_with_demo(args: list[str]) -> None:
    """Invariant: --demo with a trailing --vps is refused, as with a leading one."""
    code, out = _cli(args)
    assert code == 2
    assert "cannot be combined with --demo" in out


def test_cli_trailing_vps_still_refuses_a_non_console_command() -> None:
    """Invariant: `hermes serve --vps` is refused like `hermes --vps serve`."""
    code, out = _cli(["serve", "--vps"])
    assert (code, "not 'serve'" in out) == (2, True)


class _Script:
    """Runner fake that plays a list of results in order and counts the calls."""

    def __init__(self, results: list[SshResult | BaseException]) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, argv: list[str]) -> SshResult:
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _Tty(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _loop(
    tmp_path: Path,
    results: list[SshResult | BaseException],
    stdout: io.StringIO | None = None,
    sleep_error: BaseException | None = None,
) -> tuple[int, str, _Script, list[float]]:
    script = _Script(results)
    waited: list[float] = []
    err = io.StringIO()

    def sleep(seconds: float) -> None:
        waited.append(seconds)
        if sleep_error is not None:
            raise sleep_error

    code = run_vps(
        "positions",
        [],
        err,
        console_commands=_CONSOLE,
        env={"HERMES_VPS_KEY": str(_key(tmp_path))},
        runner=script,
        sleep=sleep,
        stdout=stdout if stdout is not None else _Tty(False),
    )
    return code, err.getvalue(), script, waited


_DROPS = [
    "Connection to hermes-01 closed by remote host.",
    "client_loop: send disconnect: Connection reset",
    "packet_write_wait: Connection to 100.1.2.3 port 22: Broken pipe",
    "Timeout, server not responding.",
    "Connection to hermes-01 closed.",
]
_LOST = "connection lost · reconnecting in {}s (Ctrl+C to stop)\n"


def test_keepalive_options_are_in_the_argv() -> None:
    """Invariant: a dead link is noticed in about 30 s (15 s interval x 2 misses)."""
    argv = build_ssh_argv(VpsSettings("h", "u", Path("k")), "status", [])
    assert argv[argv.index("ServerAliveInterval=15") - 1] == "-o"
    assert argv[argv.index("ServerAliveCountMax=2") - 1] == "-o"


@pytest.mark.parametrize("stderr", _DROPS)
def test_a_dropped_connection_is_retried_after_a_two_second_wait(
    tmp_path: Path, stderr: str
) -> None:
    """Invariant: each connection-drop marker on exit 255 reconnects once, then ends on exit 0."""
    code, err, script, waited = _loop(tmp_path, [SshResult(255, stderr), SshResult(0, "")])
    assert (code, script.calls, waited, err) == (0, 2, [2], _LOST.format(2))


def test_backoff_doubles_and_caps_at_thirty_seconds(tmp_path: Path) -> None:
    """Invariant: repeated unreachable retries wait 2, 4, 8, 16, 30, 30."""
    drop = SshResult(255, "Timeout, server not responding.")
    down = SshResult(255, "ssh: connect to host hermes-01 port 22: Connection timed out")
    results: list[SshResult | BaseException] = [drop, *[down] * 5, SshResult(0, "")]
    code, _, script, waited = _loop(tmp_path, results)
    assert (code, script.calls) == (0, 7)
    assert waited == [2, 4, 8, 16, 30, 30]


def test_backoff_restarts_after_a_new_drop(tmp_path: Path) -> None:
    """Invariant: a session that connected and dropped again waits 2 s, not the grown delay."""
    drop = SshResult(255, "Timeout, server not responding.")
    down = SshResult(255, "ssh: connect to host hermes-01 port 22: Connection refused")
    _, _, _, waited = _loop(tmp_path, [drop, down, down, drop, SshResult(0, "")])
    assert waited == [2, 4, 8, 2]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (SshResult(255, "root@hermes-01: Permission denied (publickey)."), 255),
        (SshResult(255, "ssh: connect to host hermes-01 port 22: Connection timed out"), 255),
        (SshResult(255, "Connection reset by 100.1.2.3 port 22"), 255),
        (SshResult(255, "Host key verification failed."), 255),
        (SshResult(127, "Connection to hermes-01 closed."), 127),
        (SshResult(1, "Connection to hermes-01 closed by remote host."), 1),
        (SshResult(0, ""), 0),
    ],
)
def test_non_drop_results_never_retry(tmp_path: Path, result: SshResult, expected: int) -> None:
    """Invariant: auth, first-try unreachable, deploy-needed and a remote exit never retry."""
    code, err, script, waited = _loop(tmp_path, [result])
    assert (code, script.calls, waited) == (expected, 1, [])
    assert "reconnecting" not in err


def test_auth_failure_after_a_drop_stops_at_once(tmp_path: Path) -> None:
    """Invariant: a reconnect that fails authentication ends the loop with the key hint."""
    drop = SshResult(255, "Timeout, server not responding.")
    auth = SshResult(255, "Permission denied (publickey).")
    code, err, script, waited = _loop(tmp_path, [drop, auth])
    assert (code, script.calls, waited) == (255, 2, [2])
    assert "authentication to root@hermes-01 failed" in err


def test_refused_command_never_reaches_the_runner_or_retries(tmp_path: Path) -> None:
    """Invariant: a refused command exits 2 before ssh; nothing is retried."""
    script = _Script([])
    err = io.StringIO()
    code = run_vps(
        "serve",
        [],
        err,
        console_commands=_CONSOLE,
        env={"HERMES_VPS_KEY": str(_key(tmp_path))},
        runner=script,
        sleep=lambda _s: None,
        stdout=_Tty(True),
    )
    assert (code, script.calls) == (2, 0)


def test_ctrl_c_during_the_wait_exits_130(tmp_path: Path) -> None:
    """Invariant: Ctrl+C while waiting to reconnect ends the loop with 130, no more runs."""
    drop = SshResult(255, "Timeout, server not responding.")
    code, _, script, waited = _loop(tmp_path, [drop], sleep_error=KeyboardInterrupt())
    assert (code, script.calls, waited) == (130, 1, [2])


def test_ctrl_c_during_ssh_exits_130(tmp_path: Path) -> None:
    """Invariant: a KeyboardInterrupt out of the runner is a clean 130."""
    code, _, script, waited = _loop(tmp_path, [KeyboardInterrupt()])
    assert (code, script.calls, waited) == (130, 1, [])


def test_terminal_reset_is_written_after_every_exit_on_a_tty(tmp_path: Path) -> None:
    """Invariant: the alt-screen-off and show-cursor codes follow each ssh exit on a TTY."""
    tty = _Tty(True)
    drop = SshResult(255, "Timeout, server not responding.")
    _loop(tmp_path, [drop, SshResult(0, "")], stdout=tty)
    assert tty.getvalue() == "\x1b[?1049l\x1b[?25h" * 2


def test_terminal_reset_is_not_written_when_stdout_is_not_a_tty(tmp_path: Path) -> None:
    """Invariant: piped output never receives escape sequences."""
    pipe = _Tty(False)
    _loop(tmp_path, [SshResult(0, "")], stdout=pipe)
    assert pipe.getvalue() == ""
