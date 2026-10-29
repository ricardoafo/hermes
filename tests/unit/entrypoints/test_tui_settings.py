"""The console's saved settings file (issue #711, theme switch).

Invariants:

- a saved theme choice reads back as the same choice;
- a missing, malformed or unknown file reads as "nothing picked", never an error;
- saving creates the folder and writes one ``theme = "..."`` line.

Every test writes under pytest's tmp_path, never the real home folder.
"""

from pathlib import Path

import pytest

from hermes.entrypoints.tui_settings import (
    DEFAULT_SETTINGS_PATH,
    ConsoleSettings,
    ThemeChoice,
    load_settings,
    save_settings,
)


def test_default_path_is_the_operators_config_folder() -> None:
    """The file lives at ~/.config/hermes/console.toml."""
    assert Path("~/.config/hermes/console.toml") == DEFAULT_SETTINGS_PATH


@pytest.mark.parametrize("choice", list(ThemeChoice))
def test_saved_choice_reads_back(tmp_path: Path, choice: ThemeChoice) -> None:
    """Saving a choice and loading it gives the same choice."""
    path = tmp_path / "console.toml"
    save_settings(path, ConsoleSettings(theme=choice))
    assert load_settings(path) == ConsoleSettings(theme=choice)


def test_save_writes_one_theme_line_and_creates_the_folder(tmp_path: Path) -> None:
    """The folder is created and the file holds exactly `theme = "control"`."""
    path = tmp_path / "hermes" / "console.toml"
    save_settings(path, ConsoleSettings(theme=ThemeChoice.CONTROL))
    assert path.read_text(encoding="utf-8") == 'theme = "control"\n'


def test_missing_file_means_nothing_picked(tmp_path: Path) -> None:
    """No file: no theme was picked by hand."""
    assert load_settings(tmp_path / "absent.toml") == ConsoleSettings(theme=None)


@pytest.mark.parametrize(
    "content", ["theme = ", 'theme = "sepia"\n', "theme = 3\n", "\xff\xfe"], ids=str
)
def test_bad_file_means_nothing_picked(tmp_path: Path, content: str) -> None:
    """Malformed TOML, an unknown name or a non-string value read as nothing picked."""
    path = tmp_path / "console.toml"
    path.write_text(content, encoding="latin-1")
    assert load_settings(path) == ConsoleSettings(theme=None)
