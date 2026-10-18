"""``hermes`` subcommands, one module per command.

To add a command, add a module to this package that defines ``COMMAND``::

    from hermes.entrypoints.commands import Command

    def _run(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int: ...

    COMMAND = Command(name="example", summary="One line for --help.", run=_run)

:func:`discover` imports every module here and collects its ``COMMAND``, and
:mod:`hermes.entrypoints.cli` dispatches from that. No shared list names the
commands, so two PRs that each add a command never edit the same file (#457).

Reference: issue #457; openspec/archive/457-cli-command-registry.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass

__all__ = ["Command", "CommandRegistryError", "collect", "discover"]


class CommandRegistryError(RuntimeError):
    """A command module has no COMMAND, or names a different command."""


@dataclass(frozen=True, slots=True)
class Command:
    """One ``hermes`` subcommand.

    Attributes:
        name: The token after ``hermes``.
        summary: One line for ``hermes --help``.
        run: Called with the residual argv, ``stream=`` (stdout) and ``err=``
            (stderr, for ERROR lines); returns the exit code.
        stub: True for a placeholder that is not implemented yet.
        wants_demo: True when ``run`` also takes ``demo=``, the scenario of a
            leading ``hermes --demo SCENARIO`` (``None`` without one).
        json: True when the command (or one of its subcommands) prints one
            JSON document with ``--json``; ``hermes --help`` lists these.
    """

    name: str
    summary: str
    run: Callable[..., int]
    stub: bool = False
    wants_demo: bool = False
    json: bool = False


def discover() -> dict[str, Command]:
    """Import every module in this package and return its commands by name.

    Raises:
        CommandRegistryError: See :func:`collect`.
    """
    modules = [
        (info.name, importlib.import_module(f"{__name__}.{info.name}"))
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)
        if not info.name.startswith("_")
    ]
    return collect(modules)


def collect(modules: Iterable[tuple[str, object]]) -> dict[str, Command]:
    """Return the ``COMMAND`` of each ``(module name, module)`` pair, by name.

    A command's name must equal its module's name with ``_`` read as ``-``
    (``archive_check.py`` declares ``archive-check``), so the file system keeps
    names unique and a new command is exactly one new file.

    Raises:
        CommandRegistryError: A module has no ``COMMAND`` of type
            :class:`Command`, or its name differs from the module's.
    """
    found: dict[str, Command] = {}
    for module_name, module in modules:
        command = getattr(module, "COMMAND", None)
        if not isinstance(command, Command):
            raise CommandRegistryError(f"commands.{module_name} must define COMMAND = Command(...)")
        if command.name != module_name.replace("_", "-"):
            raise CommandRegistryError(
                f"commands.{module_name} declares {command.name!r}; "
                "name the module after the command"
            )
        found[command.name] = command
    return found
