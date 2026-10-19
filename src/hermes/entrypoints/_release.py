"""The running release, read from the ``current`` symlink (shared by version, status, serve).

``scripts/deploy/install_release.sh`` flips ``/opt/hermes/current`` to the new
release directory; its name is the release. Kept apart from
:mod:`hermes.entrypoints.version` so ``hermes serve`` can log it at start-up
without importing the version command (which imports serve).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

__all__ = ["DEFAULT_RELEASE_LINK", "UNKNOWN_RELEASE", "ReleaseInfo", "read_release"]

DEFAULT_RELEASE_LINK: Final = Path("/opt/hermes/current")
UNKNOWN_RELEASE: Final = "unknown"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """The running release as the ``current`` symlink shows it.

    Attributes:
        name: Name of the directory ``current`` points at, or ``unknown``.
        live_since: When the link was last flipped (its own mtime, UTC), or
            ``None`` when the release is unknown.
    """

    name: str
    live_since: datetime | None


def read_release(link: Path = DEFAULT_RELEASE_LINK) -> ReleaseInfo:
    """Read the running release from the ``current`` symlink; never raises.

    Args:
        link: The ``current`` symlink; injectable for tests.

    Returns:
        The release name and when the link was flipped; ``unknown`` and no
        time when the link is missing or dangling.
    """
    try:
        if not link.is_symlink():
            return ReleaseInfo(UNKNOWN_RELEASE, None)
        target = link.resolve()
        if not target.exists():
            return ReleaseInfo(UNKNOWN_RELEASE, None)
        flipped = datetime.fromtimestamp(link.lstat().st_mtime, tz=UTC)
    except OSError:
        return ReleaseInfo(UNKNOWN_RELEASE, None)
    return ReleaseInfo(target.name, flipped)
