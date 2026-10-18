"""Read a local clone's history with the ``git`` command.

Every call is read-only (``cat-file``, ``rev-parse``, ``merge-base``) and
passes the commit and path as separate arguments, never through a shell. A
commit that starts with ``-`` is refused so it cannot be read as an option.

Reference: ADR-026 §1, §2; :mod:`hermes.ports.git_history`.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from hermes.domain.errors import InfraError

__all__ = ["SubprocessGit"]

_TIMEOUT_S = 30


def _ref(commit: str) -> str:
    if not commit or commit.startswith("-") or any(c.isspace() for c in commit):
        raise ValueError(f"not a commit: {commit!r}")
    return commit


@dataclass(frozen=True, slots=True)
class SubprocessGit:
    """:class:`~hermes.ports.git_history.GitHistoryPort` over the clone at ``repo``."""

    repo: Path

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 - fixed git argv, no shell
                ["git", "-C", str(self.repo), *args],  # noqa: S607 - git from PATH, as in CI
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InfraError(f"git {args[0]} failed: {exc}") from exc

    def file_at(self, commit: str, path: str) -> bytes | None:
        """Return the bytes of ``path`` at ``commit``, or None when it does not exist there."""
        if self.object_id(commit, path) is None:
            return None
        done = self._git("cat-file", "blob", f"{_ref(commit)}:{path}")
        return done.stdout if done.returncode == 0 else None

    def object_id(self, commit: str, path: str) -> str | None:
        """Return the git object id of ``path`` at ``commit``, or None."""
        done = self._git("rev-parse", "--verify", "--quiet", f"{_ref(commit)}:{path}")
        if done.returncode != 0:
            return None
        return done.stdout.decode("ascii").strip()

    def is_ancestor(self, commit: str, of: str) -> bool:
        """Return True when ``commit`` exists and is ``of`` or one of its ancestors."""
        done = self._git("merge-base", "--is-ancestor", _ref(commit), _ref(of))
        return done.returncode == 0
