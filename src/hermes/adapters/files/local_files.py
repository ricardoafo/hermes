"""The local file system as a :class:`~hermes.ports.files.FilesPort`.

Reference: GitHub issue #1186.
"""

import hashlib
from pathlib import Path
from typing import Final

__all__ = ["LocalFiles"]

_CHUNK: Final = 1 << 16
"""Hash in 64 KiB reads, so a large data file is never held in memory whole."""


class LocalFiles:
    """Read, hash and append files on the local disk.

    Satisfies :class:`~hermes.ports.files.FilesPort`.
    """

    def read_bytes(self, path: Path) -> bytes:
        """Return the file's bytes."""
        return path.read_bytes()

    def read_text(self, path: Path) -> str:
        """Return the file's UTF-8 text."""
        return path.read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        """Return whether anything exists at ``path``."""
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        """Return whether ``path`` is a directory."""
        return path.is_dir()

    def append_text(self, path: Path, text: str) -> None:
        """Append ``text`` in UTF-8 to an existing file in one write.

        Raises:
            FileNotFoundError: ``path`` does not exist; an append never creates a file.
        """
        with path.open("r+", encoding="utf-8") as fh:
            fh.seek(0, 2)
            fh.write(text)

    def sha256(self, path: Path) -> str:
        """Return the SHA-256 hex digest of the file's bytes."""
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def files_under(self, directory: Path) -> tuple[Path, ...]:
        """Return every file below ``directory``, at any depth, in sorted path order."""
        return tuple(sorted(p for p in directory.rglob("*") if p.is_file()))

    def resolve(self, path: Path) -> Path:
        """Return ``path`` made absolute, with symbolic links resolved."""
        return path.resolve()
