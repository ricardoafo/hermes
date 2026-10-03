"""``hermes health``: probe one integration, or every registered one."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from hermes.entrypoints import _health as health_module
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


def _run(rest: list[str], *, stream: Any) -> int:
    """Handle the ``hermes health`` subcommand family.

    Two forms:

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

    Returns:
        Process exit code — 0 on OK, 1 on adapter down, 2 on unknown
        integration, 3 on missing configuration.
    """
    if not rest:
        results = health_module.run_all(health_env())
        stream.write("# hermes health (all integrations)\n")
        health_module.render_table_multi(results, stream)
        return health_module.aggregate_exit_code(results)

    integration, *_extra = rest
    check = health_module.get_check(integration)
    if check is None:
        available = ", ".join(health_module.registered_integrations()) or "(none)"
        stream.write(f"ERROR: unknown integration {integration!r}. Available: {available}\n")
        return 2

    result = check(health_env())
    stream.write(f"# hermes health {integration}\n")
    health_module.render_table(result, stream)
    return result.exit_code


COMMAND = Command(
    name="health",
    summary=(
        "Health-check probe. `hermes health` (no arg) probes every "
        "registered integration; `hermes health <integration>` probes one."
    ),
    run=_run,
)
