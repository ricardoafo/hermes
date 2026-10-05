"""Top-level `hermes` CLI dispatcher.

Each subcommand lives in its own module under
:mod:`hermes.entrypoints.commands` and declares a ``COMMAND``. This module
discovers them, prints ``--help`` from their summaries and dispatches. No
list here names the commands, so adding one never edits this file (#457).

Design notes
------------

- **Argparse (stdlib)**: no extra dep just for a top-level dispatch.
  Subcommands own their own parsers and receive the residual argv.
- **Exit codes**: 0 success, 2 usage / unimplemented. Subcommands may
  return other codes (e.g. ``preview run_send`` returns 3 on prod refusal;
  ``health <int>`` returns 3 on missing config, 1 on adapter down;
  ``serve`` returns 1 on a runtime failure and 3 on a config error).
- **Entry point**: wired via ``[project.scripts]`` in ``pyproject.toml``
  as ``hermes = "hermes.entrypoints.cli:main"``.
- **Sentry init at startup**: ``main()`` initializes the Sentry SDK
  from the two env vars ``HERMES_SENTRY__DSN`` +
  ``HERMES_SENTRY__ENVIRONMENT`` BEFORE dispatching to any subcommand,
  so unhandled exceptions in the subcommand are captured by Sentry's
  global excepthook. Reads the env directly (not via ``load_config``)
  to keep the CLI runnable on a fresh clone that has not yet populated
  the YAML files. An empty DSN is a silent no-op (integration
  disabled). Init failures (bad DSN, SDK error) are swallowed inside
  :func:`sentry_integration.initialize` — the CLI never crashes on
  Sentry issues.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import os
import sys
import textwrap
from typing import Any

from hermes.adapters.console import available_scenarios
from hermes.entrypoints import console as console_module
from hermes.entrypoints import version as version_module
from hermes.entrypoints import vps as vps_module
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command, discover
from hermes.infrastructure import sentry_integration

__all__ = ["main"]

_FLAG_VPS = "--vps"


def _leading_demo_scenario(args: list[str]) -> str | None:
    """Return the scenario of a leading ``--demo`` option, or ``None``."""
    try:
        return console_module.split_demo_flag(args).scenario
    except console_module.DemoFlagError:
        return None


def _print_top_help(stream: Any, commands: dict[str, Command]) -> None:
    """Print the top-level `hermes` help message to ``stream``."""
    width = max((len(name) for name in commands), default=0)
    demo_commands = ", ".join(sorted(n for n, c in commands.items() if c.wants_demo))
    lines = [
        "hermes — crypto trading engine: paper trading, ledger and operator tools",
        "",
        "Usage: hermes [--demo SCENARIO] <command> [args]",
        "",
        "Commands:",
    ]
    for name in sorted(commands):
        lines.append(f"  {name:{width}s}  {commands[name].summary}")
    lines.append("")
    lines.append("Options:")
    lines.append("  --demo SCENARIO  Show console screens on demo data (DEMO DATA tag).")
    lines.append(f"                   Scenarios: {', '.join(available_scenarios())}")
    lines.extend(_accepted_by(demo_commands))
    lines.append("  --vps            Run a console command on the VPS over ssh (read-only).")
    lines.append("                   Settings: HERMES_VPS_HOST, HERMES_VPS_USER, HERMES_VPS_KEY.")
    lines.append("  --json           One JSON document on stdout.")
    lines.extend(_accepted_by(", ".join(sorted(n for n, cmd in commands.items() if cmd.json))))
    lines.append("")
    lines.append("Run `hermes <command> --help` for a command's options and exit codes.")
    lines.append("")
    stream.write("\n".join(lines))


def _unknown_command_message(subcommand: str, commands: dict[str, Command]) -> str:
    """Return the error line for an unknown ``subcommand``, with a suggestion if close."""
    message = f"ERROR: unknown subcommand {subcommand!r}."
    close = difflib.get_close_matches(subcommand, list(commands), n=1)
    if close:
        message += f" Did you mean {close[0]!r}?"
    return message + " Run `hermes --help`.\n"


def _utf8_stdout() -> Any:
    """Return ``sys.stdout`` reconfigured for UTF-8 output.

    On Windows the default console encoding is ``cp1252`` which cannot
    render the emoji glyphs the templates use. Reconfiguring the
    underlying buffer as UTF-8 keeps the output correct without asking
    the operator to set ``PYTHONIOENCODING`` manually. Falls back to
    the raw stream if reconfiguration is unavailable (e.g. a pipe with
    no ``reconfigure`` method).
    """
    stdout = sys.stdout
    reconfigure = getattr(stdout, "reconfigure", None)
    if callable(reconfigure):
        # Suppress UnsupportedOperation for odd streams (e.g. pytest capture)
        # and ValueError for unusual buffer types where reconfigure exists but
        # refuses UTF-8. Both are safe fall-throughs — the default encoding
        # will just miss the emoji rendering, not crash the CLI.
        with contextlib.suppress(io.UnsupportedOperation, ValueError):
            reconfigure(encoding="utf-8")
    return stdout


def _initialize_sentry_from_env(env: dict[str, str]) -> None:
    """Initialize Sentry from ``env`` — no-op when DSN is empty.

    Reads ``HERMES_SENTRY__DSN`` + ``HERMES_SENTRY__ENVIRONMENT`` directly
    from the env snapshot rather than routing through ``load_config`` so
    the CLI stays runnable on a fresh clone that has not yet populated
    every YAML file. Delegates to
    :func:`sentry_integration.initialize`, which itself:

    - Is a no-op when ``dsn`` is empty (integration disabled).
    - Never raises — any SDK-init failure is logged and swallowed.

    Args:
        env: Env-var snapshot (defaults come from ``os.environ`` in
            production; tests inject a controlled dict).
    """
    dsn = env.get("HERMES_SENTRY__DSN", "").strip()
    environment = env.get("HERMES_SENTRY__ENVIRONMENT", "dev").strip() or "dev"
    sentry_integration.initialize(dsn=dsn, environment=environment)


def _demo_then_vps(args: list[str]) -> bool:
    """True for ``--demo SCENARIO --vps ...``, the other order of the refused pair."""
    try:
        return vps_module.split_vps_flag(console_module.split_demo_flag(args).rest)[0]
    except console_module.DemoFlagError:
        return False


def _vps_exit(args: list[str], err: Any) -> int | None:
    """Handle a ``--vps`` run, before or after the command; ``None`` when not asked for."""
    leading, rest = vps_module.split_vps_flag(args)
    trailing = _FLAG_VPS in rest
    if leading and trailing:
        err.write("ERROR: give --vps once, before or after the command.\n")
        return 2
    if trailing:
        rest = [arg for arg in rest if arg != _FLAG_VPS]
    if leading or trailing:
        return _run_on_vps(rest, err, demo_first=False)
    if _demo_then_vps(args):
        return _run_on_vps(args, err, demo_first=True)
    return None


def _run_on_vps(args: list[str], err: Any, *, demo_first: bool) -> int:
    """Run the console command after ``--vps`` on the VPS; refuse ``--demo`` alongside it."""
    if not args:
        err.write("ERROR: --vps needs a console command, e.g. `hermes --vps positions`.\n")
        return 2
    if demo_first or any(arg == "--demo" or arg.startswith("--demo=") for arg in args):
        err.write("ERROR: --vps cannot be combined with --demo; the demo runs locally.\n")
        return 2
    return vps_module.run_vps(
        args[0],
        args[1:],
        err,
        console_commands=console_module.console_commands(),
        env=os.environ,
    )


def _split_late_demo(rest: list[str]) -> tuple[str | None, list[str]]:
    """Remove a ``--demo SCENARIO`` or ``--demo=SCENARIO`` placed after the command.

    Raises:
        DemoFlagError: ``--demo`` has no scenario name.
    """
    kept: list[str] = []
    scenario: str | None = None
    items = iter(rest)
    for arg in items:
        if arg == "--demo" or arg.startswith("--demo="):
            value = next(items, None) if arg == "--demo" else None
            split = console_module.split_demo_flag([arg] if value is None else [arg, value])
            scenario = split.scenario
        else:
            kept.append(arg)
    return scenario, kept


def _run_console(
    command: Command, rest: list[str], out: Any, err: Any, demo_scenario: str | None
) -> int:
    """Run a console command, taking a ``--demo`` given after it as well as before."""
    try:
        late, rest = _split_late_demo(rest)
    except console_module.DemoFlagError as exc:
        err.write(f"ERROR: {exc}. Scenarios: {', '.join(available_scenarios())}\n")
        return 2
    if late is not None and demo_scenario is not None:
        err.write("ERROR: give --demo once, before or after the command.\n")
        return 2
    return command.run(rest, stream=out, demo=late or demo_scenario, err=err)


def _dispatch(args: list[str], out: Any, err: Any, demo_scenario: str | None) -> int:
    """Run the version flag, the help, or the named subcommand."""
    if args[:1] in (["--version"], ["-V"]):
        return version_module.main([], stream=out)

    commands = discover()
    if not args or args[0] in {"-h", "--help"}:
        _print_top_help(out, commands)
        return 0

    subcommand, *rest = args
    command = commands.get(subcommand)
    if command is None:
        err.write(_unknown_command_message(subcommand, commands))
        return 2
    if command.wants_demo:
        return _run_console(command, rest, out, err, demo_scenario)
    return command.run(rest, stream=out, err=err)


def main(
    argv: list[str] | None = None, *, stream: Any | None = None, err: Any | None = None
) -> int:
    """Dispatch the invoked subcommand.

    Initializes Sentry (from env) BEFORE dispatching so unhandled
    exceptions in the subcommand are shipped to Sentry via its global
    excepthook. Init failures are silently swallowed inside
    :func:`sentry_integration.initialize` — a Sentry outage MUST NOT
    crash the CLI.

    Args:
        argv: Argument list — defaults to ``sys.argv[1:]``.
        stream: Output stream — defaults to a UTF-8-reconfigured
            ``sys.stdout``. Injected for tests.
        err: Stream for ERROR lines — defaults to ``sys.stderr``, so stdout
            holds only a command's real output (#543). Injected for tests.

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    out = stream if stream is not None else _utf8_stdout()
    errors = resolve_err(err)

    # Initialize Sentry first — before any subcommand can raise. If the
    # DSN is empty, this is a no-op (integration disabled). Any SDK
    # init error is caught + logged inside sentry_integration.initialize
    # so the CLI never crashes on a Sentry failure.
    _initialize_sentry_from_env(dict(os.environ))

    vps_exit = _vps_exit(args, errors)
    if vps_exit is not None:
        return vps_exit

    demo_scenario = _leading_demo_scenario(args)
    demo_exit, args = console_module.apply_demo_flag(args, out, errors)
    if demo_exit is not None:
        return demo_exit

    return _dispatch(args, out, errors, demo_scenario)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _accepted_by(names: str) -> list[str]:
    """Wrap a "Commands that accept it" note under the option column."""
    indent = " " * 19
    return textwrap.wrap(
        f"Commands that accept it: {names}",
        width=88,
        initial_indent=indent,
        subsequent_indent=indent + "  ",
    )
