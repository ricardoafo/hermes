"""A config directory whose roster names an unknown ``signal:`` (issue #1119).

Research funnel contract §2a: an unknown ``signal:`` value is refused at
config load, never replaced by the default kind, and each entry point exits
with its own code. The tests of ``hermes parity``, the decision replay and
``run_portfolio_backtest.py`` share this config and the error lines they
must print.
"""

import shutil
from pathlib import Path
from typing import Final

CONFIG_DIR: Final = Path(__file__).resolve().parents[2] / "config"
UNKNOWN_SIGNAL: Final = "no_such_signal"
_ANCHOR: Final = "    exit_pricing: stop_order # pre-registration-v1.md §5.3, §6.2\n"


def config_with_unknown_signal(tmp: Path) -> Path:
    """Copy ``config/`` under ``tmp`` with the enabled v1 entry naming :data:`UNKNOWN_SIGNAL`.

    Args:
        tmp: Directory to copy into.

    Returns:
        The copied config directory.

    Raises:
        AssertionError: ``strategies.yaml`` no longer holds the v1 line the
            signal goes under.

    """
    target = tmp / "config"
    shutil.copytree(CONFIG_DIR, target)
    strategies = target / "strategies.yaml"
    text = strategies.read_text(encoding="utf-8")
    if text.count(_ANCHOR) != 1:
        raise AssertionError(f"expected one v1 exit_pricing line in {strategies}")
    strategies.write_text(
        text.replace(_ANCHOR, f"{_ANCHOR}    signal: {UNKNOWN_SIGNAL}\n"), encoding="utf-8"
    )
    return target


def refusal_lines(config_dir: Path) -> tuple[str, str]:
    """Return the first and last stderr lines of the config-load refusal.

    The first line is the ERROR line naming the file; the last names the
    unknown signal and the kinds that exist.

    Args:
        config_dir: The directory :func:`config_with_unknown_signal` returned.

    Returns:
        ``(first line, last line)``.

    """
    return (
        f"ERROR: Config error: validation failed for {config_dir / 'strategies.yaml'} "
        "against StrategiesConfig: 1 validation error for StrategiesConfig",
        f"  Value error, unknown signal '{UNKNOWN_SIGNAL}'; known: donchian_breakout "
        "[type=value_error]",
    )
