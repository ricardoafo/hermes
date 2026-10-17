"""Git history port: read files and object ids at a commit.

A research batch binds a signing precursor, hypothesis cards and candidate
code to commits (ADR-026 §1, §2). Its checks read those through this port so
they stay pure; :mod:`hermes.adapters.git_history.subprocess_git` reads a
local clone.

Reference: ADR-026 §1, §2; code-conventions.md §5 (Ports as Protocols).
"""

from typing import Protocol, runtime_checkable

__all__ = ["GitHistoryPort"]


@runtime_checkable
class GitHistoryPort(Protocol):
    """Read-only view of one repository's history."""

    def file_at(self, commit: str, path: str) -> bytes | None:
        """Return the bytes of ``path`` at ``commit``, or None when it does not exist there."""
        ...

    def object_id(self, commit: str, path: str) -> str | None:
        """Return the git object id of ``path`` (file or directory) at ``commit``, or None."""
        ...

    def is_ancestor(self, commit: str, of: str) -> bool:
        """Return True when ``commit`` exists and is ``of`` or one of its ancestors."""
        ...
