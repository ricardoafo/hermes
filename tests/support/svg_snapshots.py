"""Shared helpers for the console's SVG snapshot tests."""

from typing import ClassVar

from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex


class NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``boot_neon_80x24``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"
