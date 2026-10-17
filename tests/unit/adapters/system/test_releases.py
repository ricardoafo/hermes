"""The release directory reader (issue #717).

Invariants:

- every ``versions/<sha12>`` directory is listed, newest finished install
  first, an unfinished install (no ``.installed``) last;
- ``previous`` is the target of the ``previous`` link; the live release is
  the one the injected reader (``read_release``) returns;
- "not available here" (``None``) without a ``versions`` directory;
- reading changes nothing on disk.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.system.releases import ReleaseDirectoryReader
from hermes.application.console.system import LiveRelease

_LIVE = LiveRelease("2bc2798a1f00", datetime(2026, 10, 2, 14, 2, tzinfo=UTC))


def _release(root: Path, name: str, installed: int | None) -> Path:
    path = root / "versions" / name
    path.mkdir(parents=True)
    if installed is not None:
        marker = path / ".installed"
        marker.touch()
        os.utime(marker, (installed, installed))
    return path


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")


def _tree(root: Path) -> Path:
    _release(root, "9f01a3c5d2e1", 1_759_000_000)
    previous = _release(root, "77de210b4c3a", 1_758_000_000)
    _release(root, "2bc2798a1f00", 1_759_400_000)
    _release(root, "000aaa111bbb", None)
    _link(root / "previous", previous)
    return root


def test_releases_are_listed_newest_install_first_unfinished_last(tmp_path: Path) -> None:
    """Order follows the .installed marker's time; no marker sorts last."""
    history = ReleaseDirectoryReader(live=lambda: _LIVE, root=_tree(tmp_path)).releases()
    assert history is not None
    assert history.names() == ("2bc2798a1f00", "9f01a3c5d2e1", "77de210b4c3a", "000aaa111bbb")


def test_install_times_come_from_the_marker(tmp_path: Path) -> None:
    """The newest release was installed at its marker's mtime; the unfinished has none."""
    history = ReleaseDirectoryReader(live=lambda: _LIVE, root=_tree(tmp_path)).releases()
    assert history is not None
    assert (history.entries[0].installed_at, history.entries[-1].installed_at) == (
        datetime.fromtimestamp(1_759_400_000, tz=UTC),
        None,
    )


def test_previous_is_the_link_target_and_live_comes_from_the_reader(tmp_path: Path) -> None:
    """The previous link points at 77de210b4c3a; live is what read_release returned."""
    history = ReleaseDirectoryReader(live=lambda: _LIVE, root=_tree(tmp_path)).releases()
    assert history is not None
    assert (history.previous, history.live, history.rollback_target("2bc2798a1f00")) == (
        "77de210b4c3a",
        _LIVE,
        "77de210b4c3a",
    )


def test_no_previous_link_means_no_previous(tmp_path: Path) -> None:
    """A first deploy has no previous link, so no rollback target."""
    _release(tmp_path, "2bc2798a1f00", 1_759_400_000)
    history = ReleaseDirectoryReader(live=lambda: _LIVE, root=tmp_path).releases()
    assert history is not None
    assert (history.previous, history.rollback_target("2bc2798a1f00")) == (None, None)


def test_not_available_without_a_versions_directory(tmp_path: Path) -> None:
    """A PC has no /opt/hermes/versions: None, so the panel says not available here."""
    found = ReleaseDirectoryReader(live=lambda: _LIVE, root=tmp_path).releases()
    assert (found, (tmp_path / "versions").exists()) == (None, False)


def test_reading_changes_nothing_on_disk(tmp_path: Path) -> None:
    """The reader lists and resolves; every file and link stays as it was."""
    root = _tree(tmp_path)
    before = sorted((str(p), p.lstat().st_mtime_ns) for p in root.rglob("*"))
    ReleaseDirectoryReader(live=lambda: _LIVE, root=root).releases()
    after = sorted((str(p), p.lstat().st_mtime_ns) for p in root.rglob("*"))
    assert after == before
    assert sorted(Path(path).name for path, _ in after) == [
        ".installed",
        ".installed",
        ".installed",
        "000aaa111bbb",
        "2bc2798a1f00",
        "77de210b4c3a",
        "9f01a3c5d2e1",
        "previous",
        "versions",
    ]
