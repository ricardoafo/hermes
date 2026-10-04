"""``hermes health``: probe one integration, or every registered one."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TextIO

from dotenv import dotenv_values

from hermes.entrypoints import _health as health_module
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.commands import Command
from hermes.infrastructure.config.loader import DEFAULT_DOTENV_PATH

__all__ = ["COMMAND", "health_env"]


def health_env(dotenv_path: Path = DEFAULT_DOTENV_PATH) -> dict[str, str]:
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


_USAGE = """Usage: hermes health [<integration>] [--json]

Probe every registered integration, or just <integration>.

Options:
  --json      one JSON document: an object per integration (name, status,
              detail) plus the overall exit_code
  -h, --help  show this help

Exit codes:
  0  every probe OK
  1  an adapter is down
  2  bad usage or unknown integration
  3  an integration is not configured (and none is down)"""


def _parse(rest: list[str], stream: Any, err: TextIO) -> tuple[bool, list[str]] | int:
    """Split ``rest`` into ``(as_json, positionals)``, or return an exit code.

    Returns 0 after printing usage for ``-h``/``--help`` and 2 for an
    unknown option.
    """
    as_json = False
    positional: list[str] = []
    for arg in rest:
        if arg in ("-h", "--help"):
            stream.write(_USAGE + "\n")
            return 0
        if arg == "--json":
            as_json = True
        elif arg.startswith("-"):
            err.write(f"ERROR: unknown option {arg!r}.\n{_USAGE}\n")
            return 2
        else:
            positional.append(arg)
    return as_json, positional


def _run(rest: list[str], *, stream: Any, err: TextIO | None = None) -> int:
    """Handle the ``hermes health`` subcommand family.

    Two forms, each with an optional ``--json``:

    - ``hermes health`` (no arg) → runs EVERY registered check via
      :func:`health_module.run_all` and prints ONE consolidated
      multi-row table. Exit code aggregates per
      :func:`health_module.aggregate_exit_code` (0 all OK, 1 any FAIL,
      3 any NOT_CONFIGURED and no FAIL).
    - ``hermes health <integration>`` → runs a single probe and prints
      the two-column key/value table.

    Args:
        rest: Residual argv after the ``health`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code: 0 on OK or ``--help``, 1 on adapter down, 2 on
        bad usage or unknown integration, 3 on missing configuration.
    """
    err = resolve_err(err)
    parsed = _parse(rest, stream, err)
    if isinstance(parsed, int):
        return parsed
    as_json, positional = parsed

    env = health_env()
    if not positional:
        results = health_module.run_all(env)
        if as_json:
            return health_module.render_json(results, env, stream)
        stream.write("# hermes health (all integrations)\n")
        health_module.render_table_multi(results, stream)
        return health_module.aggregate_exit_code(results)

    integration = positional[0]
    check = health_module.get_check(integration)
    if check is None:
        available = ", ".join(health_module.registered_integrations()) or "(none)"
        err.write(f"ERROR: unknown integration {integration!r}. Available: {available}\n")
        return 2

    result = check(env)
    if as_json:
        return health_module.render_json([result], env, stream)
    stream.write(f"# hermes health {integration}\n")
    health_module.render_table(result, stream)
    return result.exit_code


COMMAND = Command(
    name="health",
    summary=(
        "Health-check probe. `hermes health` (no arg) probes every "
        "registered integration; `hermes health <integration>` probes one. "
        "`--json` prints the same data as JSON."
    ),
    run=_run,
)
