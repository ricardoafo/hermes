"""The console's saved settings: ``~/.config/hermes/console.toml`` on the operator's PC.

Only the theme choice is saved today (issue #711, theme switch). A missing,
unreadable or malformed file reads as "nothing picked", so the console always
opens; the file is rewritten whole on every save.
"""

from __future__ import annotations

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
    """

    theme: ThemeChoice | None = None


def load_settings(path: Path) -> ConsoleSettings:
    """Read the settings file.

    Args:
        path: The TOML file; ``~`` is expanded.

    Returns:
        The saved settings, or empty settings when the file is missing,
        unreadable, not TOML, or names an unknown theme.
    """
    try:
        data = tomllib.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return ConsoleSettings()
    raw = data.get(_THEME_KEY)
    try:
        return (
            ConsoleSettings(theme=ThemeChoice(raw)) if isinstance(raw, str) else ConsoleSettings()
        )
    except ValueError:
        return ConsoleSettings()


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
    target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
