"""Files port: the reads and appends the application layer makes on local files.

The application layer judges evidence files, reads the signed
pre-registration and appends to the lab notebook, but it never touches the
file system itself (issue #1186): every read, hash and append goes through
this port, so a test can hand it any file system and an installed release
reads the same paths the entrypoint chose.

Implementations live in :mod:`hermes.adapters.files`.

Reference: code-conventions.md §5 (Ports as Protocols); GitHub issue #1186.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["FilesPort"]


@runtime_checkable
class FilesPort(Protocol):
    """Contract for local file reads, hashes and appends.

    Text is UTF-8. A file that cannot be read or written raises
    :class:`OSError` (or a subclass); text that is not UTF-8 raises
    :class:`UnicodeDecodeError`.
    """

    def read_bytes(self, path: Path) -> bytes:
        """Return the file's bytes."""
        ...

    def read_text(self, path: Path) -> str:
        """Return the file's text."""
        ...

    def exists(self, path: Path) -> bool:
        """Return whether anything exists at ``path``."""
        ...

    def is_dir(self, path: Path) -> bool:
        """Return whether ``path`` is a directory."""
        ...

    def append_text(self, path: Path, text: str) -> None:
        """Append ``text`` to the end of an existing file in one write."""
        ...

    def sha256(self, path: Path) -> str:
        """Return the SHA-256 hex digest of the file's bytes."""
        ...

    def files_under(self, directory: Path) -> tuple[Path, ...]:
        """Return every file below ``directory``, at any depth, in sorted path order."""
        ...

    def resolve(self, path: Path) -> Path:
        """Return ``path`` made absolute, with symbolic links resolved."""
        ...
