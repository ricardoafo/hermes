"""The application layer touches no file itself: every read goes through the files port (#1186).

Invariant: no module under ``src/hermes/application`` calls ``open()`` or a
file-system method of :class:`pathlib.Path` (read, write, list, stat,
resolve). A call on the injected port (an object named ``files`` or
``_files``) is the one way in, so a test can hand any file system to the
code and an installed release reads only the paths its entrypoint chose.

The import side of the rule is the ``ports-import-only-domain`` and
``application-imports-only-domain-and-ports`` import-linter contracts.
"""

import ast
from typing import Final

from tests.support.paths import REPO_ROOT

_APPLICATION: Final = REPO_ROOT / "src" / "hermes" / "application"

_FILE_SYSTEM_METHODS: Final = frozenset(
    {
        "chmod",
        "exists",
        "glob",
        "is_dir",
        "is_file",
        "iterdir",
        "mkdir",
        "open",
        "read_bytes",
        "read_text",
        "rename",
        "resolve",
        "rglob",
        "stat",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)

_PORT_NAMES: Final = frozenset({"files", "_files"})


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def file_system_calls(source: str) -> list[tuple[int, str]]:
    """Return ``(line, call)`` for each direct file-system call in ``source``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            found.append((node.lineno, "open"))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _FILE_SYSTEM_METHODS
            and _receiver_name(func.value) not in _PORT_NAMES
        ):
            found.append((node.lineno, func.attr))
    return found


def test_the_application_layer_makes_no_file_system_call() -> None:
    """Every module under application/ reaches files only through ``FilesPort``."""
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): calls
        for path in sorted(_APPLICATION.rglob("*.py"))
        if (calls := file_system_calls(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}


def test_the_guard_sees_each_kind_of_direct_call() -> None:
    """A path read, an ``open()`` and a nested receiver are caught; a port call is not."""
    source = (
        "def f(path, files, self):\n"
        "    path.read_text()\n"
        "    open(path)\n"
        "    self.config.path.resolve()\n"
        "    files.read_bytes(path)\n"
        "    self._files.exists(path)\n"
    )

    assert file_system_calls(source) == [(2, "read_text"), (3, "open"), (4, "resolve")]
