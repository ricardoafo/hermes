"""Unit tests for scripts/dev/console-catalog.py (#851).

Invariants under test:

- every console snapshot in the repo has a complete caption, so the catalog
  shows every state the tests review;
- snapshot names map to the right view, theme and size in all three shapes;
- a missing caption, an empty field or an unknown screen is reported;
- the page shows every snapshot once and escapes caption text;
- the deploy checklist lists exactly the changed views, and says when a view
  was removed or when there is no earlier release.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.paths import REPO_ROOT


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "console_catalog", REPO_ROOT / "scripts" / "dev" / "console-catalog.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["console_catalog"] = module
    spec.loader.exec_module(module)
    return module


CATALOG = _load()
_CAPTION = {
    "screen": "Home",
    "state": "Two positions",
    "means": "hermes is live.",
    "check": "The total is bright.",
}


def test_every_console_snapshot_has_a_complete_caption() -> None:
    """INV: no snapshot is left out of the catalog for want of a caption."""
    shots = CATALOG.find_shots()

    assert shots
    assert CATALOG.problems(shots, CATALOG.load_captions()) == []


@pytest.mark.parametrize(
    ("relative", "view", "theme", "size"),
    [
        (
            "test_tui_snapshots/home_ok_neon_160x45.svg",
            "test_tui_snapshots/home_ok",
            "neon",
            "160x45",
        ),
        (
            "test_tui_snapshots/theme_picker_control_80x24.svg",
            "test_tui_snapshots/theme_picker",
            "control",
            "80x24",
        ),
        (
            "test_tui/test_svg_snapshot_down_80x24.svg",
            "test_tui/test_svg_snapshot_down",
            "",
            "80x24",
        ),
        (
            "test_tui_home_screen/test_svg_snapshot_empty[240x60].svg",
            "test_tui_home_screen/test_svg_snapshot_empty",
            "",
            "240x60",
        ),
    ],
)
def test_a_snapshot_name_gives_its_view_theme_and_size(
    relative: str, view: str, theme: str, size: str
) -> None:
    """INV: all three naming shapes map to one caption key, a theme and a size."""
    shot = CATALOG.parse_shot(CATALOG.SNAPSHOTS / relative)

    assert (shot.view, shot.theme, shot.size) == (view, theme, size)


def _shot(tmp_path: Path, name: str) -> object:
    path = tmp_path / "test_x" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return CATALOG.parse_shot(path, tmp_path)


def test_a_missing_caption_an_empty_field_and_an_unknown_screen_are_reported(
    tmp_path: Path,
) -> None:
    """INV: each gap in the captions is one line naming the view."""
    shots = [
        _shot(tmp_path, "a_neon_80x24.svg"),
        _shot(tmp_path, "b_neon_80x24.svg"),
        _shot(tmp_path, "c_neon_80x24.svg"),
    ]
    captions = {
        "test_x/b": {**_CAPTION, "check": " "},
        "test_x/c": {**_CAPTION, "screen": "Lobby"},
    }

    found = CATALOG.problems(shots, captions)

    assert [line.split(":")[0] for line in found] == ["test_x/a", "test_x/b", "test_x/c"]
    assert "'check'" in found[1]
    assert "'Lobby'" in found[2]


def test_the_page_shows_every_snapshot_once_and_escapes_captions(tmp_path: Path) -> None:
    """INV: one picture per snapshot; caption text cannot inject markup."""
    shots = [
        _shot(tmp_path, f"a_{theme}_{size}.svg")
        for theme in ("neon", "control")
        for size in ("80x24", "120x36")
    ]
    captions = {"test_x/a": {**_CAPTION, "means": "<script>alert(1)</script>"}}

    page = CATALOG.render_page(shots, captions, release="v1", since=None, changed=None)

    assert page.count("<img ") == len(shots)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_the_checklist_lists_changed_and_removed_views() -> None:
    """INV: the checklist names each changed view once, and a removed one as removed."""
    prefix = "tests/unit/entrypoints/__snapshots__/"
    changed = CATALOG.changed_views(
        [
            f"{prefix}test_tui_snapshots/home_ok_neon_80x24.svg",
            f"{prefix}test_tui_snapshots/home_ok_control_80x24.svg",
            f"{prefix}test_tui_snapshots/gone_neon_80x24.svg",
            f"{prefix}test_render_snapshots.ambr",
            "src/hermes/entrypoints/tui.py",
        ]
    )
    captions = {"test_tui_snapshots/home_ok": _CAPTION}

    page = CATALOG.render_page([], captions, release="v2", since="v1", changed=changed)

    assert changed == ["test_tui_snapshots/gone", "test_tui_snapshots/home_ok"]
    assert "test_tui_snapshots/gone</code>: removed in this release" in page
    assert "Home · Two positions: The total is bright." in page


def test_without_an_earlier_release_the_checklist_says_check_everything() -> None:
    """INV: no release to compare with never reads as "nothing changed"."""
    page = CATALOG.render_page([], {}, release="abc123", since=None, changed=None)

    assert "No earlier release to compare with: check every view." in page


@pytest.mark.parametrize(
    ("caption", "command"),
    [
        ({"scenario": "empty"}, "hermes --demo empty tui"),
        (
            {"scenario": "ok-two-positions", "keys": "6"},
            "hermes --demo ok-two-positions tui, then press 6",
        ),
        (
            {"demo": "hermes tui --db <a new, empty ledger>"},
            "hermes tui --db <a new, empty ledger>",
        ),
    ],
)
def test_each_state_says_how_to_open_it(caption: dict[str, str], command: str) -> None:
    """INV: a caption's scenario and keys become the demo command the operator types."""
    assert CATALOG.demo_command(caption) == command


def test_check_mode_passes_on_this_repo(capsys: pytest.CaptureFixture[str]) -> None:
    """INV: ``--check`` exits 0 when every snapshot is captioned, as CI would run it."""
    assert CATALOG.main(["--check"]) == 0
    assert "every view captioned" in capsys.readouterr().out
