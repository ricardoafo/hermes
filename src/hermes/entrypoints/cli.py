"""Top-level `hermes` CLI dispatcher.

Registers subcommands the future integration PRs will fill in. Today
``preview`` renders templates and ``health <integration>`` probes an
integration adapter; ``serve`` and ``simulate`` remain stubs that print
"not yet implemented" and exit with code 2 so composition scripts fail
loudly if they attempt to call them.

Design notes
------------

- **Argparse (stdlib)**: no extra dep just for a top-level dispatch.
  Subcommands own their own parsers (see :mod:`hermes.entrypoints.preview`)
  and receive the residual argv.
- **Exit codes**: 0 success, 2 usage / unimplemented. Subcommands may
  return other codes (e.g. ``preview run_send`` returns 3 on prod refusal;
  ``health <int>`` returns 3 on missing config, 1 on adapter down).
- **Entry point**: wired via ``[project.scripts]`` in ``pyproject.toml``
  as ``hermes = "hermes.entrypoints.cli:main"``.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from typing import Any, Final

from hermes.entrypoints import _health as health_module
from hermes.entrypoints import preview as preview_module

__all__ = ["main"]

_SUBCOMMAND_HELP: Final[dict[str, str]] = {
    "preview": "Render + optionally send notification templates.",
    "serve": "Run the trading engine (stub — implemented in a future PR).",
    "health": "Health-check probe for an integration (e.g. `hermes health telegram`).",
    "simulate": "Replay historical scenarios (stub — implemented in a future PR).",
}

# ``health`` is intentionally NOT in this set — it has a real implementation
# below (per-integration probe). PR E will replace the "no-arg" branch with
# a registry walk.
_STUB_SUBCOMMANDS: Final[frozenset[str]] = frozenset({"serve", "simulate"})


def _print_top_help(stream: Any) -> None:
    """Print the top-level `hermes` help message to ``stream``."""
    lines = [
        "hermes — crypto trading engine (MVP1 scaffold)",
        "",
        "Usage: hermes <command> [args]",
        "",
        "Commands:",
    ]
    for name in sorted(_SUBCOMMAND_HELP):
        lines.append(f"  {name:10s} {_SUBCOMMAND_HELP[name]}")
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


def main(argv: list[str] | None = None, *, stream: Any | None = None) -> int:
    """Dispatch the invoked subcommand.

    Args:
        argv: Argument list — defaults to ``sys.argv[1:]``.
        stream: Output stream — defaults to a UTF-8-reconfigured
            ``sys.stdout``. Injected for tests.

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    out = stream if stream is not None else _utf8_stdout()

    if not args or args[0] in {"-h", "--help"}:
        _print_top_help(out)
        return 0

    subcommand, *rest = args
    if subcommand == "preview":
        return preview_module.main(rest, stream=out)
    if subcommand == "health":
        return _dispatch_health(rest, stream=out)
    if subcommand in _STUB_SUBCOMMANDS:
        out.write(
            f"ERROR: `hermes {subcommand}` is not yet implemented. "
            f"Tracked for a future integration PR.\n"
        )
        return 2
    out.write(f"ERROR: unknown subcommand {subcommand!r}. Run `hermes --help`.\n")
    return 2


def _dispatch_health(rest: list[str], *, stream: Any) -> int:
    """Handle the ``hermes health <integration>`` subcommand.

    The no-arg form prints a usage line + the list of registered
    integrations and exits 2. PR E will replace that branch with a
    registry walk that probes every integration in one pass.

    Args:
        rest: Residual argv after the ``health`` token.
        stream: Output stream.

    Returns:
        Process exit code — 0 on OK, 1 on adapter down, 2 on usage
        error / unknown integration, 3 on missing configuration.
    """
    if not rest:
        integrations = ", ".join(health_module.registered_integrations()) or "(none)"
        stream.write(
            f"Usage: hermes health <integration>\n  Registered integrations: {integrations}\n"
        )
        return 2

    integration, *_extra = rest
    check = health_module.get_check(integration)
    if check is None:
        available = ", ".join(health_module.registered_integrations()) or "(none)"
        stream.write(f"ERROR: unknown integration {integration!r}. Available: {available}\n")
        return 2

    result = check(dict(os.environ))
    stream.write(f"# hermes health {integration}\n")
    health_module.render_table(result, stream)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
