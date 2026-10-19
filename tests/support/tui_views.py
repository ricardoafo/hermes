"""Console screens as a reviewed picture or as reviewable text (issue #1194).

One SVG per screen is the visual record (Neon at 160x45); every other theme and
breakpoint is checked on text: what each cell says and which colours the screen
uses. A text snapshot shows its change in a diff; a 100 KB SVG does not.
"""

from typing import ClassVar, Final

from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex
from textual.app import App

__all__ = ["NamedSVG", "View", "assert_view", "capture", "is_record"]

_RECORD_SIZE: Final = "160x45"
_NOT_RECORD_THEMES: Final = ("control",)


class View(str):
    """A screen's SVG, with its text and colours in :attr:`digest`."""

    digest: str

    def __new__(cls, svg: str, digest: str) -> View:
        """Make the view; ``svg`` is its string value."""
        view = super().__new__(cls, svg)
        view.digest = digest
        return view


class NamedSVG(SVGImageSnapshotExtension):
    """Store each SVG snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``home_ok_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def capture(app: App[None]) -> View:
    """Capture the shown screen as an SVG and as text plus the colours it uses.

    Args:
        app: A running test app.

    Returns:
        The view; its string value is the repository-clean SVG.

    """
    strips = app.screen._compositor.render_strips()
    text = "\n".join(strip.text.rstrip() for strip in strips)
    colours: set[str] = set()
    for strip in strips:
        for segment in strip:
            style = segment.style
            if style is None:
                continue
            for colour in (style.color, style.bgcolor):
                if colour is not None:
                    colours.add(colour.get_truecolor().hex)
    palette = " ".join(sorted(colours))
    return View(_repo_clean(app.export_screenshot()), f"{text}\n---\ncolours: {palette}\n")


def is_record(name: str) -> bool:
    """Return whether a view named ``<view>_<theme>_<cols>x<rows>`` keeps its SVG."""
    return _RECORD_SIZE in name and not any(theme in name for theme in _NOT_RECORD_THEMES)


def assert_view(
    snapshot: SnapshotAssertion, view: str, *, name: str | None = None, record: bool | None = None
) -> None:
    """Compare a view with its snapshot: the SVG for the record, the text for the rest.

    Args:
        snapshot: The syrupy fixture.
        view: What :func:`capture` returned.
        name: The snapshot name; ``None`` uses the test's own.
        record: Whether to keep the SVG; ``None`` decides from ``name``.

    """
    assert isinstance(view, View)
    keep = is_record(name or "") if record is None else record
    if keep:
        extension = NamedSVG if name else SVGImageSnapshotExtension
        shot = (
            snapshot(extension_class=extension, name=name)
            if name
            else snapshot(extension_class=extension)
        )
        assert view == shot
    else:
        text_shot = snapshot(name=name) if name else snapshot
        assert view.digest == text_shot
