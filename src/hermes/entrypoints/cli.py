"""Top-level `hermes` CLI dispatcher.

Registers subcommands the future integration PRs will fill in. Today
``preview`` renders templates, ``health <integration>`` probes an
integration adapter, and ``serve`` runs the shadow bar loop via
the composition root in :mod:`hermes.entrypoints.serve`. ``simulate``
remains a stub that prints "not yet implemented" and exits with code
2 so composition scripts fail loudly if they attempt to call it.

Design notes
------------

- **Argparse (stdlib)**: no extra dep just for a top-level dispatch.
  Subcommands own their own parsers (see :mod:`hermes.entrypoints.preview`)
  and receive the residual argv.
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

import argparse
import asyncio
import contextlib
import io
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from dotenv import dotenv_values

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.console import available_scenarios
from hermes.entrypoints import _health as health_module
from hermes.entrypoints import activity as activity_module
from hermes.entrypoints import archive_check as archive_check_module
from hermes.entrypoints import certify as certify_module
from hermes.entrypoints import console as console_module
from hermes.entrypoints import journal as journal_module
from hermes.entrypoints import preview as preview_module
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints import status as status_module
from hermes.infrastructure import sentry_integration
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_DOTENV_PATH,
    load_config,
)

__all__ = ["main"]


def _health_env(dotenv_path: Path = DEFAULT_DOTENV_PATH) -> dict[str, str]:
    """Return the env snapshot ``hermes health`` probes, including ``.env.local``.

    Mirrors :func:`load_config`: a missing dotenv file is a no-op and a
    real environment variable always wins over the file. Unlike
    ``load_config`` it does not mutate :data:`os.environ`.

    Args:
        dotenv_path: Dotenv file to merge under the process environment.

    Returns:
        The merged snapshot.
    """
    from_file = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    return {**from_file, **os.environ}


_SUBCOMMAND_HELP: Final[dict[str, str]] = {
    "preview": "Render + optionally send notification templates.",
    "serve": "Run the paper-trading engine until SIGTERM/SIGINT.",
    "health": (
        "Health-check probe. `hermes health` (no arg) probes every "
        "registered integration; `hermes health <integration>` probes one."
    ),
    "certify": "Evaluate the next certification gate for a strategy (pre-reg §7).",
    "activity": "What changed, grouped by day; `--all` adds bar checks and signals.",
    "archive-check": "Compare yesterday's live bars with the Binance archive (read-only).",
    "journal": "Record, list and review Code Owner decisions (decision journal).",
    "status": "Read-only progress check: verdict, last and next bar per stream.",
    "simulate": "Replay historical scenarios (stub — implemented in a future PR).",
}

# ``health`` and ``serve`` are intentionally NOT in this set — they have real
# implementations below (registry walk / composition root respectively).
_STUB_SUBCOMMANDS: Final[frozenset[str]] = frozenset({"simulate"})


def _print_top_help(stream: Any) -> None:
    """Print the top-level `hermes` help message to ``stream``."""
    lines = [
        "hermes — crypto trading engine (MVP1 scaffold)",
        "",
        "Usage: hermes [--demo SCENARIO] <command> [args]",
        "",
        "Commands:",
    ]
    for name in sorted(_SUBCOMMAND_HELP):
        lines.append(f"  {name:10s} {_SUBCOMMAND_HELP[name]}")
    lines.append("")
    lines.append("Options:")
    lines.append("  --demo SCENARIO  Show console screens on demo data (DEMO DATA tag).")
    lines.append(f"                   Scenarios: {', '.join(available_scenarios())}")
    lines.append("")
    stream.write("\n".join(lines))


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


def _leading_demo_scenario(args: list[str]) -> str | None:
    """Return the scenario of a leading ``--demo`` option, or ``None``."""
    try:
        return console_module.split_demo_flag(args).scenario
    except console_module.DemoFlagError:
        return None


def main(argv: list[str] | None = None, *, stream: Any | None = None) -> int:
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

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    out = stream if stream is not None else _utf8_stdout()

    # Initialize Sentry first — before any subcommand can raise. If the
    # DSN is empty, this is a no-op (integration disabled). Any SDK
    # init error is caught + logged inside sentry_integration.initialize
    # so the CLI never crashes on a Sentry failure.
    _initialize_sentry_from_env(dict(os.environ))

    demo_scenario = _leading_demo_scenario(args)
    demo_exit, args = console_module.apply_demo_flag(args, out)
    if demo_exit is not None:
        return demo_exit

    if not args or args[0] in {"-h", "--help"}:
        _print_top_help(out)
        return 0

    subcommand, *rest = args
    table: dict[str, Callable[..., int]] = {
        "preview": preview_module.main,
        "health": _dispatch_health,
        "serve": _dispatch_serve,
        "certify": certify_module.main,
        "activity": lambda rest, *, stream: activity_module.run_activity(
            rest, stream, demo_flag=demo_scenario, env=os.environ
        ),
        "archive-check": archive_check_module.main,
        "journal": journal_module.main,
        "status": status_module.main,
    }
    dispatch = table.get(subcommand)
    if dispatch is not None:
        return dispatch(rest, stream=out)
    if subcommand in _STUB_SUBCOMMANDS:
        out.write(
            f"ERROR: `hermes {subcommand}` is not yet implemented. "
            f"Tracked for a future integration PR.\n"
        )
        return 2
    out.write(f"ERROR: unknown subcommand {subcommand!r}. Run `hermes --help`.\n")
    return 2


def _dispatch_health(rest: list[str], *, stream: Any) -> int:
    """Handle the ``hermes health`` subcommand family.

    Two forms:

    - ``hermes health`` (no arg) → runs EVERY registered check via
      :func:`health_module.run_all` and prints ONE consolidated
      multi-row table. Exit code aggregates per
      :func:`health_module.aggregate_exit_code` (0 all OK, 1 any FAIL,
      3 any NOT_CONFIGURED and no FAIL).
    - ``hermes health <integration>`` → runs a single probe and prints
      the two-column key/value table (existing behaviour preserved).

    Args:
        rest: Residual argv after the ``health`` token.
        stream: Output stream.

    Returns:
        Process exit code — 0 on OK, 1 on adapter down, 2 on unknown
        integration, 3 on missing configuration.
    """
    if not rest:
        results = health_module.run_all(_health_env())
        stream.write("# hermes health (all integrations)\n")
        health_module.render_table_multi(results, stream)
        return health_module.aggregate_exit_code(results)

    integration, *_extra = rest
    check = health_module.get_check(integration)
    if check is None:
        available = ", ".join(health_module.registered_integrations()) or "(none)"
        stream.write(f"ERROR: unknown integration {integration!r}. Available: {available}\n")
        return 2

    result = check(_health_env())
    stream.write(f"# hermes health {integration}\n")
    health_module.render_table(result, stream)
    return result.exit_code


def _dispatch_serve(rest: list[str], *, stream: Any) -> int:
    """Handle the ``hermes serve`` subcommand.

    Parses ``--config-dir`` and ``--db``, loads the composite
    :class:`~hermes.infrastructure.config.HermesConfig`, checks it can run
    the shadow loop, wires the composition root, installs signal handlers,
    and runs the bar loop. Returns 0 on a clean shutdown, 1 on a runtime
    failure, 2 on argument-parse failure, 3 on a config load or startup
    check failure. The environment tier comes from ``HERMES_ENV`` only.

    Args:
        rest: Residual argv after the ``serve`` token.
        stream: Output stream — errors are written here so tests can
            capture them without touching real stdout.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes serve", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes serve` arguments.\n")
        return 2

    if parsed.help:
        stream.write(
            "Usage: hermes serve [--config-dir DIR] [--db PATH]\n"
            "\n"
            "Runs the shadow bar loop until SIGTERM or SIGINT arrives.\n"
            "The environment tier comes from HERMES_ENV.\n"
            "Exit codes: 0 clean shutdown, 1 runtime failure, 2 usage, 3 config.\n"
        )
        return 0

    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    try:
        serve_module.check_startup(
            config,
            dict(os.environ),
            certification_store=JsonCertificationStore(
                serve_module.certification_dir_for(parsed.db)
            ),
        )
    except serve_module.StartupError as exc:
        stream.write(f"ERROR: startup check failed: {exc}\n")
        return 3

    return asyncio.run(_serve_main(config, db_path=parsed.db))


async def _serve_main(config: Any, *, db_path: Path) -> int:
    """Wire the composition root and run the bar loop to its exit code.

    Extracted from :func:`_dispatch_serve` so tests can drive the
    composition + run steps without going through argparse.

    Args:
        config: The loaded :class:`~hermes.infrastructure.config.HermesConfig`.
        db_path: Path to the SQLite ledger file.

    Returns:
        :func:`~hermes.entrypoints.serve.run`'s exit code: 0 on clean
        shutdown, 1 on a runtime failure (including a failed build).
    """
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    try:
        composition = serve_module.build_composition(config, db_path=db_path)
    except Exception as exc:
        serve_module.report_failure(exc)
        return 1
    serve_module.install_signal_handlers(loop, shutdown)
    return await serve_module.run(composition, shutdown)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
