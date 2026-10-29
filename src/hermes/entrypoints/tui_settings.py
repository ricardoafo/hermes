"""The console's saved settings: ``~/.config/hermes/console.toml`` on the operator's PC.

The theme choice (issue #711) and ``show_values`` (issue #1056, the Lab screen
may show the trial log's figures) are saved. A missing,
unreadable or malformed file reads as "nothing picked", so the console always
opens; the file is rewritten whole on every save.
"""

import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_SETTINGS_PATH",
    "ConsoleSettings",
    "ThemeChoice",
    "load_settings",
    "save_settings",
]

DEFAULT_SETTINGS_PATH: Final = Path("~/.config/hermes/console.toml")
_THEME_KEY: Final = "theme"
_VALUES_KEY: Final = "show_values"


class ThemeChoice(StrEnum):
    """What the operator picked with T, the command palette or ``--theme``.

    Auto shows Neon by day and the night theme from 23:00 to 08:00 local.
    """

    NEON = "neon"
    CONTROL = "control"
    AUTO = "auto"


@dataclass(frozen=True)
class ConsoleSettings:
    """What the operator picked by hand.

    Attributes:
        theme: The theme choice, or ``None`` when none was picked (Auto applies).
        show_values: The operator allows the Lab screen to show the trial log's
            figures. Only ever set by hand in the file; the console never
            writes it ``True`` itself, and an AI session ignores it.
    """

    theme: ThemeChoice | None = None
    show_values: bool = False


def load_settings(path: Path) -> ConsoleSettings:
    """Read the settings file.

    Args:
        path: The TOML file; ``~`` is expanded.

    Returns:
        The saved settings, or empty settings when the file is missing,
        unreadable or not TOML; an unknown theme reads as none picked.
    """
    try:
        data = tomllib.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return ConsoleSettings()
    show_values = data.get(_VALUES_KEY) is True
    raw = data.get(_THEME_KEY)
    try:
        theme = ThemeChoice(raw) if isinstance(raw, str) else None
    except ValueError:
        theme = None
    return ConsoleSettings(theme=theme, show_values=show_values)


def save_settings(path: Path, settings: ConsoleSettings) -> None:
    """Write the settings file, creating its folder.

    Args:
        path: The TOML file; ``~`` is expanded.
        settings: What to save; a ``None`` theme removes the key.

    Raises:
        OSError: The file or its folder cannot be written.
    """
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [] if settings.theme is None else [f'{_THEME_KEY} = "{settings.theme.value}"']
    if settings.show_values:
        lines.append(f"{_VALUES_KEY} = true")
    target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
