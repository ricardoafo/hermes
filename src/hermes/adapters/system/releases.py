"""The release directory as the System screen's release reader (issue #717).

``scripts/deploy/install_release.sh`` keeps every release under
``/opt/hermes/versions/<sha12>/``, writes ``.installed`` there last, and points
``current`` and ``previous`` at releases. This reader lists that directory and
reads the two links; the live release comes from the same reader
``hermes status`` uses (``read_release``), handed in by the entrypoint so this
adapter imports no entrypoint. It only reads; it never flips a link.

Off the VPS there is no ``versions`` directory and the reader returns ``None``.

Reference: openspec/changes/717-system-screen/change.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from hermes.application.console.system import LiveRelease, ReleaseEntry, ReleaseHistory

__all__ = ["DEFAULT_RELEASE_ROOT", "ReleaseDirectoryReader"]

DEFAULT_RELEASE_ROOT: Final = Path("/opt/hermes")
_VERSIONS: Final = "versions"
_PREVIOUS: Final = "previous"
_MARKER: Final = ".installed"


def _installed_at(release: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp((release / _MARKER).stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _link_target(link: Path) -> str | None:
    try:
        if not link.is_symlink():
            return None
        return link.resolve().name
    except OSError:
        return None


def _newest_first(entry: ReleaseEntry) -> tuple[bool, float, str]:
    at = entry.installed_at
    return (at is None, -(at.timestamp() if at else 0.0), entry.name)


@dataclass(frozen=True, slots=True)
class ReleaseDirectoryReader:
    """List the releases on disk.

    Attributes:
        live: Returns the live release (``read_release`` in production).
        root: The install root holding ``versions/`` and ``previous``.
    """

    live: Callable[[], LiveRelease]
    root: Path = DEFAULT_RELEASE_ROOT

    def releases(self) -> ReleaseHistory | None:
        """Return the releases, newest install first, or ``None`` without a release directory.

        Returns:
            The live release, the ``previous`` target and every release
            directory; an unfinished install is listed last with no time.
        """
        versions = self.root / _VERSIONS
        try:
            children = [child for child in versions.iterdir() if child.is_dir()]
        except OSError:
            return None
        entries = sorted(
            (ReleaseEntry(child.name, _installed_at(child)) for child in children),
            key=_newest_first,
        )
        return ReleaseHistory(
            live=self.live(),
            previous=_link_target(self.root / _PREVIOUS),
            entries=tuple(entries),
        )
