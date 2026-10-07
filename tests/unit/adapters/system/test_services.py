"""The ``systemctl show`` service reader (issue #717).

Invariants:

- one read-only ``systemctl show`` call asks about every unit and timer; the
  argv never holds start, stop, restart or reload;
- each unit's state, and its timer's next run only while the timer is armed;
- "not available here" (``None``) without systemctl, when the call fails, or
  when systemd knows none of the hermes units (a dev box with systemd);
- ``--timestamp=unix`` values parse to UTC; ``0``, ``n/a`` and empty are no time.

No test starts a real systemctl: the runner is a fake.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from hermes.adapters.system.services import SystemctlServiceReader, parse_show, parse_timestamp
from hermes.application.console.system import SERVICE_UNITS

_SYSTEMCTL = "/usr/bin/systemctl"
_NEXT = 1759507200  # 2025-10-03 16:00:00 UTC
_SINCE = 1759305600  # 2025-10-01 08:00:00 UTC


class _Runner:
    """Records each argv and returns canned stdout."""

    def __init__(self, output: str | None) -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str | None:
        self.calls.append(list(argv))
        return self.output


def _block(unit: str, **props: str) -> str:
    lines = [f"Id={unit}", *(f"{key}={value}" for key, value in props.items())]
    return "\n".join(lines)


def _show_output() -> str:
    blocks = [
        _block(
            "hermes.service",
            LoadState="loaded",
            ActiveState="active",
            SubState="running",
            StateChangeTimestamp=f"@{_SINCE}",
        ),
        _block(
            "hermes-bot.service", LoadState="not-found", ActiveState="inactive", SubState="dead"
        ),
        _block("hermes-daily.service", LoadState="loaded", ActiveState="failed", SubState="failed"),
        _block(
            "hermes-daily.timer",
            LoadState="loaded",
            ActiveState="active",
            SubState="waiting",
            NextElapseUSecRealtime=f"@{_NEXT}",
            LastTriggerUSec="n/a",
        ),
        _block("hermes-backup.timer", LoadState="loaded", ActiveState="inactive"),
    ]
    return "\n\n".join(blocks) + "\n"


def _reader(output: str | None) -> tuple[SystemctlServiceReader, _Runner]:
    runner = _Runner(output)
    return SystemctlServiceReader(executable=_SYSTEMCTL, run=runner), runner


def test_one_read_only_show_call_asks_about_every_unit_and_timer() -> None:
    """The argv is systemctl show with unix times, the services, then the timers."""
    reader, runner = _reader(_show_output())
    reader.services()
    argv = runner.calls[0]
    assert (len(runner.calls), argv[:3], argv[4:]) == (
        1,
        [_SYSTEMCTL, "show", "--timestamp=unix"],
        [unit.unit for unit in SERVICE_UNITS] + [u.timer for u in SERVICE_UNITS if u.timer],
    )


def test_the_argv_never_holds_a_command_that_changes_a_unit() -> None:
    """Only show runs: no start, stop, restart, reload, enable or kill."""
    reader, runner = _reader(_show_output())
    reader.services()
    verbs = {"start", "stop", "restart", "reload", "enable", "disable", "kill", "reset-failed"}
    assert verbs & set(runner.calls[0]) == set()


def test_states_and_the_armed_timer_next_run_are_read() -> None:
    """The hermes unit runs since its change time; daily failed, its timer due at 16:00."""
    reader, _ = _reader(_show_output())
    statuses = {status.unit.name: status for status in reader.services() or ()}
    hermes, daily = statuses["hermes"], statuses["daily"]
    assert (
        hermes.state,
        hermes.since,
        daily.state,
        daily.timer_active,
        daily.next_run,
        daily.last_run,
    ) == (
        "active (running)",
        datetime(2025, 10, 1, 8, tzinfo=UTC),
        "failed (failed)",
        True,
        datetime(2025, 10, 3, 16, tzinfo=UTC),
        None,
    )


def test_a_disarmed_timer_has_no_next_run_and_a_missing_unit_is_not_installed() -> None:
    """The backup timer is inactive; the bot unit is not-found."""
    reader, _ = _reader(_show_output())
    statuses = {status.unit.name: status for status in reader.services() or ()}
    assert (
        statuses["backup"].timer_active,
        statuses["backup"].next_run,
        statuses["bot"].state,
    ) == (False, None, "not installed")


def test_not_available_without_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """No systemctl on PATH (a PC): None, and nothing runs."""
    monkeypatch.setattr("hermes.adapters.system.services.shutil.which", lambda _name: None)
    runner = _Runner(_show_output())
    reader = SystemctlServiceReader(run=runner)
    assert (reader.services(), runner.calls) == (None, [])


def test_not_available_when_the_call_fails() -> None:
    """A failed or timed-out show gives None, not an empty list."""
    reader, runner = _reader(None)
    assert (reader.services(), len(runner.calls)) == (None, 1)


def test_not_available_when_no_hermes_unit_is_installed() -> None:
    """A dev box with systemd but no hermes units reads as not available here."""
    output = "\n\n".join(_block(u.unit, LoadState="not-found") for u in SERVICE_UNITS)
    reader, runner = _reader(output)
    assert (reader.services(), len(runner.calls)) == (None, 1)


def test_timestamps_parse_to_utc_and_empty_values_are_none() -> None:
    """@seconds and bare microseconds parse; 0, n/a and empty are no time."""
    assert [parse_timestamp(v) for v in (f"@{_NEXT}", f"{_NEXT}000000", "0", "n/a", "")] == [
        datetime(2025, 10, 3, 16, tzinfo=UTC),
        datetime(2025, 10, 3, 16, tzinfo=UTC),
        None,
        None,
        None,
    ]


def test_show_output_splits_into_one_mapping_per_unit() -> None:
    """Blocks without an Id are dropped; values may contain '='."""
    parsed = parse_show("Id=a.service\nX=1=2\n\nY=3\n\nId=b.timer\n")
    assert parsed == {"a.service": {"Id": "a.service", "X": "1=2"}, "b.timer": {"Id": "b.timer"}}
