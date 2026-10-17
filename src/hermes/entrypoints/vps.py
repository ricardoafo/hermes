"""``hermes --vps <console command>``: run a console command on the VPS over SSH.

The operator types one local command; this module builds an ``ssh -t`` argv
(a list, never a local shell string), runs the console command on the VPS as the
``hermes`` user against the deployed release, and returns its exit code. Only
console commands go through (the registry's ``wants_demo`` set), so ``serve``,
``migrate`` and every other writer are refused here, before ssh starts.

Connection settings come from ``HERMES_VPS_HOST``, ``HERMES_VPS_USER`` and
``HERMES_VPS_KEY``; the defaults match the documented operator setup
(``root@hermes-01`` over Tailscale, key ``~/.ssh/hermes_vps_ed25519``). No
secret lives in the repo: the key is only a path.

Reference: openspec/changes/console-vps/change.md.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, TextIO

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_USER",
    "REMOTE_BIN",
    "REMOTE_GUARD",
    "SshResult",
    "VpsSettings",
    "build_ssh_argv",
    "local_truecolor",
    "run_vps",
    "split_vps_flag",
]

DEFAULT_HOST: Final = "hermes-01"
DEFAULT_USER: Final = "root"
REMOTE_BIN: Final = "/opt/hermes/current/.venv/bin/hermes"
_REMOTE_WORKDIR: Final = "/var/lib/hermes"
_REMOTE_ENV_FILE: Final = "/etc/hermes/env.production"
_REMOTE_DB: Final = "/var/lib/hermes/hermes.sqlite3"
_REMOTE_CONFIG_DIR: Final = "/opt/hermes/current/config"
# Same wrapper as `hermes_run` in docs/guides/vps-operations.md: load the service
# env, run in the ledger's directory, drop to the `hermes` user.
# Runs first on the VPS, as `bash -c ... _ BIN COMMAND`: an older build whose
# `--help` does not list COMMAND ("unknown subcommand") exits 127, which the local
# side maps to the deploy hint. The remote's own text travels on the TTY, which
# must stay attached to the terminal, so it cannot be read here.
REMOTE_GUARD: Final = (
    '"$1" --help 2>/dev/null | grep -q -- "^  $2 " || '
    '{ echo "hermes: $2 is not on this VPS build" >&2; exit 127; }; '
)
_REMOTE_SCRIPT: Final = REMOTE_GUARD + (
    f"set -a; . {_REMOTE_ENV_FILE}; set +a; "
    f'export HERMES_DB_PATH="${{HERMES_DB_PATH:-{_REMOTE_DB}}}" '
    f'HERMES_CONFIG_DIR="${{HERMES_CONFIG_DIR:-{_REMOTE_CONFIG_DIR}}}"; '
    f'cd {_REMOTE_WORKDIR} && exec runuser -u hermes -- "$@"'
)
# The console picks its colour depth from COLORTERM, which ssh does not forward
# and sudo would drop: without it the VPS sees only TERM=xterm-256color and the
# theme's dark background rounds to black (#992).
_REMOTE_TRUECOLOR: Final = "export COLORTERM=truecolor; "
_TRUECOLOR_VALUES: Final = frozenset({"truecolor", "24bit"})
_FLAG: Final = "--vps"
_SSH_FAILED: Final = 255
_COMMAND_NOT_FOUND: Final = 127
_CONNECT_TIMEOUT_SECONDS: Final = 10
_ALIVE_INTERVAL_SECONDS: Final = 15
_ALIVE_COUNT_MAX: Final = 2
_SIGINT_EXIT: Final = 130
_BACKOFF_SECONDS: Final = (2, 4, 8, 16, 30)
# Leave the alternate screen and show the cursor, so a dropped full-screen console
# never strands the operator's terminal.
_TERMINAL_RESET: Final = "\x1b[?1049l\x1b[?25h"

_UNREACHABLE_MARKERS: Final = (
    "could not resolve hostname",
    "connection timed out",
    "connection refused",
    "no route to host",
    "network is unreachable",
    "operation timed out",
    "connection closed by",
    "connection reset",
)
# Messages ssh prints only after it had connected. "Connection reset by <ip> port"
# is the pre-auth form (the server never accepted us), so it is excluded.
_DROP_PATTERN: Final = re.compile(
    r"connection reset(?! by \S+ port)|broken pipe|timeout, server not responding"
    r"|closed by remote host|connection to \S+ closed|client_loop: send disconnect"
    r"|connection to \S+ timed out"
)
_AUTH_MARKERS: Final = ("permission denied", "too many authentication failures")


@dataclass(frozen=True, slots=True)
class VpsSettings:
    """Where and how to reach the VPS.

    Attributes:
        host: SSH host name (the Tailscale name by default).
        user: SSH login user.
        key: Path of the private key file.
    """

    host: str
    user: str
    key: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, home: Path | None = None) -> VpsSettings:
        """Read the settings from ``env``; an empty variable counts as unset."""
        base = home if home is not None else Path.home()
        return cls(
            host=env.get("HERMES_VPS_HOST", "").strip() or DEFAULT_HOST,
            user=env.get("HERMES_VPS_USER", "").strip() or DEFAULT_USER,
            key=Path(env.get("HERMES_VPS_KEY", "").strip() or base / ".ssh" / "hermes_vps_ed25519"),
        )


@dataclass(frozen=True, slots=True)
class SshResult:
    """What the ssh process left behind.

    Attributes:
        returncode: ssh's exit code, which is the remote command's when ssh connected.
        stderr: ssh's own diagnostics; the remote's output travels on the TTY.
    """

    returncode: int
    stderr: str


SshRunner = Callable[[list[str]], SshResult]


def split_vps_flag(args: list[str]) -> tuple[bool, list[str]]:
    """Strip a leading ``--vps`` from ``args``; only the first argument is inspected."""
    if args[:1] == [_FLAG]:
        return True, args[1:]
    return False, list(args)


def build_ssh_argv(
    settings: VpsSettings, command: str, args: list[str], *, truecolor: bool = False
) -> list[str]:
    """Return the ssh argv that runs ``hermes <command> <args>`` on the VPS.

    The remote side is one string quoted with :func:`shlex.join`, so an argument
    with spaces or shell metacharacters reaches the remote command intact.

    Args:
        settings: Host, user and key.
        command: A console command name.
        args: Its arguments, forwarded verbatim.
        truecolor: The local terminal draws 24-bit colour; the remote command
            is told so through ``COLORTERM``.

    Returns:
        The argument list for :mod:`subprocess` (no shell involved locally).
    """
    script = (_REMOTE_TRUECOLOR if truecolor else "") + _REMOTE_SCRIPT
    remote = shlex.join(["bash", "-c", script, "_", REMOTE_BIN, command, *args])
    return [
        "ssh",
        "-t",
        "-i",
        str(settings.key),
        "-o",
        f"ConnectTimeout={_CONNECT_TIMEOUT_SECONDS}",
        "-o",
        f"ServerAliveInterval={_ALIVE_INTERVAL_SECONDS}",
        "-o",
        f"ServerAliveCountMax={_ALIVE_COUNT_MAX}",
        f"{settings.user}@{settings.host}",
        f"sudo {remote}",
    ]


def _tee(source: IO[str], sink: IO[str]) -> str:
    """Copy ``source`` to ``sink`` character by character and return what was copied.

    Character by character, so a prompt without a trailing newline (ssh's
    ``Enter passphrase for key ...:``) reaches the user before ssh waits for input.
    """
    seen: list[str] = []
    while char := source.read(1):
        sink.write(char)
        sink.flush()
        seen.append(char)
    return "".join(seen)


def _subprocess_runner(argv: list[str]) -> SshResult:
    """Run ssh with the terminal attached; ssh's stderr is echoed live and kept.

    stdin and stdout are inherited so the remote TTY and the passphrase prompt
    (which ssh also reads from the console itself) work; stderr is teed, never held back.
    """
    with subprocess.Popen(  # noqa: S603 - argv list, fixed ssh binary
        argv, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    ) as proc:
        assert proc.stderr is not None  # noqa: S101 - PIPE guarantees it
        seen = _tee(proc.stderr, sys.stderr)
        return SshResult(proc.wait(), seen)


def _safe_token(value: str) -> bool:
    """True for a host or user that ssh cannot read as an option."""
    return (
        bool(value) and not value.startswith("-") and all(c.isalnum() or c in "._-" for c in value)
    )


def _ssh_failure_message(result: SshResult, settings: VpsSettings) -> str:
    """Map an ssh exit-255 diagnostic to the one-line error for the operator."""
    text = result.stderr.lower()
    target = f"{settings.user}@{settings.host}"
    if any(marker in text for marker in _AUTH_MARKERS):
        return (
            f"ssh authentication to {target} failed; check the key "
            f"({settings.key}) or set HERMES_VPS_KEY / HERMES_VPS_USER."
        )
    if any(marker in text for marker in _UNREACHABLE_MARKERS):
        return f"cannot reach {settings.host}; is Tailscale connected? (or set HERMES_VPS_HOST)."
    last = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), "")
    return f"ssh to {target} failed (exit 255){': ' + last.strip().rstrip('.') if last else ''}."


def _refusal(command: str, settings: VpsSettings, console_commands: frozenset[str]) -> str | None:
    """Return why ``command`` must not reach ssh, or ``None`` when it may."""
    if command not in console_commands:
        allowed = ", ".join(sorted(console_commands))
        return f"--vps only runs console commands ({allowed}), not {command!r}."
    if not (_safe_token(settings.host) and _safe_token(settings.user)):
        return "HERMES_VPS_HOST and HERMES_VPS_USER must be plain names."
    if not settings.key.is_file():
        return f"ssh key not found at {settings.key}; set HERMES_VPS_KEY."
    return None


def _agent_hint(result: SshResult, settings: VpsSettings) -> str | None:
    """Suggest ``ssh-add`` once the passphrase prompt appeared; never required."""
    if "passphrase" not in result.stderr.lower():
        return None
    return (
        f"hint: to stop typing the passphrase, run `ssh-add {settings.key}` once "
        "(Windows: `Start-Service ssh-agent` first, as administrator)."
    )


def _exit_message(result: SshResult, settings: VpsSettings) -> str | None:
    """Return the one-line error for a failed ssh or a missing remote binary."""
    if result.returncode == _SSH_FAILED:
        return _ssh_failure_message(result, settings)
    if result.returncode == _COMMAND_NOT_FOUND:
        return f"hermes is not at {REMOTE_BIN} on the VPS; the VPS needs a deploy with the console."
    return None


def _is_drop(result: SshResult) -> bool:
    """True when ssh died with 255 because an established connection went away."""
    return (
        result.returncode == _SSH_FAILED and _DROP_PATTERN.search(result.stderr.lower()) is not None
    )


def _is_unreachable(result: SshResult) -> bool:
    """True for exit 255 with a host-unreachable diagnostic (and no auth failure)."""
    text = result.stderr.lower()
    return (
        result.returncode == _SSH_FAILED
        and not any(marker in text for marker in _AUTH_MARKERS)
        and any(marker in text for marker in _UNREACHABLE_MARKERS)
    )


def _reset_terminal(stdout: TextIO) -> None:
    """Leave the alternate screen and show the cursor, only on a real terminal."""
    if stdout.isatty():
        stdout.write(_TERMINAL_RESET)
        stdout.flush()


def _finish(result: SshResult, settings: VpsSettings, err: TextIO) -> int:
    """Report a final ssh result on ``err`` and return its exit code."""
    message = _exit_message(result, settings)
    if message is not None:
        err.write(f"ERROR: {message}\n")
    hint = _agent_hint(result, settings)
    if hint is not None:
        err.write(hint + "\n")
    return result.returncode


def local_truecolor(out: TextIO, env: Mapping[str, str], platform: str) -> bool:
    """Return whether the local terminal draws 24-bit colour.

    It does when ``out`` is a terminal and either ``COLORTERM`` says so or the
    platform is Windows, whose consoles all draw 24-bit colour since Windows 10
    and rarely set ``COLORTERM``.

    Args:
        out: The stream the remote console draws on.
        env: Environment snapshot.
        platform: ``sys.platform``.
    """
    if not out.isatty():
        return False
    return env.get("COLORTERM", "").strip().lower() in _TRUECOLOR_VALUES or platform == "win32"


def run_vps(
    command: str,
    args: list[str],
    err: TextIO,
    *,
    console_commands: frozenset[str],
    env: Mapping[str, str],
    runner: SshRunner = _subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
    stdout: TextIO | None = None,
    platform: str = sys.platform,
) -> int:
    """Run console ``command`` on the VPS and return its exit code.

    When an established connection drops (ssh exit 255 with a connection-drop
    message) the run is retried with capped exponential backoff until the operator
    presses Ctrl+C. A failure on the very first attempt that is not a drop (host
    unreachable, auth, missing key) never retries, nor does a deploy-needed (127)
    or any normal exit.

    Args:
        command: The command named after ``--vps``.
        args: Its arguments.
        err: Where one-line errors and hints go (stderr).
        console_commands: The commands allowed through; anything else is refused.
        env: Environment snapshot holding the optional ``HERMES_VPS_*`` settings.
        runner: Runs the ssh argv; injected by tests so nothing ever connects.
        sleep: Waits between reconnect attempts; injected by tests.
        stdout: Stream that gets the terminal reset after each ssh exit; defaults to
            ``sys.stdout`` and is written only when it is a TTY.
        platform: ``sys.platform``; see :func:`local_truecolor`.

    Returns:
        The remote command's exit code; 2 for a refusal, bad settings or no ssh
        client; 255 for an ssh failure (message on ``err``); 127 when the
        VPS has no console to run; 130 when Ctrl+C stops the run.
    """
    settings = VpsSettings.from_env(env)
    problem = _refusal(command, settings, console_commands)
    if problem is not None:
        err.write(f"ERROR: {problem}\n")
        return 2
    out = stdout if stdout is not None else sys.stdout
    truecolor = local_truecolor(out, env, platform)
    argv = build_ssh_argv(settings, command, args, truecolor=truecolor)
    started = False
    waits = 0
    while True:
        try:
            result = runner(argv)
        except FileNotFoundError:
            err.write("ERROR: ssh not found; install the OpenSSH client.\n")
            return 2
        except KeyboardInterrupt:
            _reset_terminal(out)
            return _SIGINT_EXIT
        _reset_terminal(out)
        if _is_drop(result):
            started = True
            waits = 0
        elif not (started and _is_unreachable(result)):
            return _finish(result, settings, err)
        delay = _BACKOFF_SECONDS[min(waits, len(_BACKOFF_SECONDS) - 1)]
        waits += 1
        err.write(f"connection lost · reconnecting in {delay}s (Ctrl+C to stop)\n")
        try:
            sleep(delay)
        except KeyboardInterrupt:
            return _SIGINT_EXIT
