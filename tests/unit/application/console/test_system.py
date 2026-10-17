"""The System screen's view objects and the actions it only shows (issue #717).

Invariants:

- a release's role is live, previous or earlier, from the two links;
- only the live release has a rollback target, and only while ``previous``
  names another release (``rollback.sh`` flips ``current`` to ``previous``);
- a unit systemd does not know reads "not installed";
- ``age_text`` never reads "ago" for a time in the future, and steps from
  minutes to hours to days at the boundaries;
- every VPS action is a command to copy, and the diagnosis is the read-only
  ``hermes status``.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.system import (
    DIAGNOSIS,
    SERVICE_UNITS,
    VPS_ACTIONS,
    LiveRelease,
    ReleaseEntry,
    ReleaseHistory,
    ReleaseRole,
    ServiceStatus,
    age_text,
)

_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_LIVE = "2bc2798a1f00"
_PREVIOUS = "9f01a3c5d2e1"
_EARLIER = "77de210b4c3a"


def _history(previous: str | None = _PREVIOUS) -> ReleaseHistory:
    return ReleaseHistory(
        live=LiveRelease(_LIVE, _NOW),
        previous=previous,
        entries=(
            ReleaseEntry(_LIVE, _NOW),
            ReleaseEntry(_PREVIOUS, _NOW - timedelta(days=2)),
            ReleaseEntry(_EARLIER, None),
        ),
    )


def test_roles_follow_the_current_and_previous_links() -> None:
    """The live link names the live release, the previous link the previous one."""
    history = _history()
    assert [history.role(name) for name in history.names()] == [
        ReleaseRole.LIVE,
        ReleaseRole.PREVIOUS,
        ReleaseRole.EARLIER,
    ]


def test_only_the_live_release_rolls_back_to_previous() -> None:
    """rollback.sh flips current to previous: the live release's target is previous."""
    history = _history()
    assert [history.rollback_target(name) for name in history.names()] == [
        _PREVIOUS,
        None,
        None,
    ]


def test_no_rollback_target_when_previous_is_unset_or_the_live_release() -> None:
    """After an automatic rollback previous equals current; rollback.sh would refuse."""
    assert (
        _history(previous=None).rollback_target(_LIVE),
        _history(previous=_LIVE).rollback_target(_LIVE),
    ) == (None, None)


def test_an_unfinished_install_is_not_complete() -> None:
    """A release without its .installed marker has no install time."""
    history = _history()
    assert [entry.complete for entry in history.entries] == [True, True, False]


def test_entry_of_a_release_that_left_the_disk_is_none() -> None:
    """A pruned release has no entry, so its detail can say it is gone."""
    history = _history()
    assert (history.entry("000000000000"), history.entry(_EARLIER)) == (
        None,
        ReleaseEntry(_EARLIER, None),
    )


def test_a_unit_systemd_does_not_know_reads_not_installed() -> None:
    """LoadState not-found shows "not installed", whatever the other fields say."""
    unit = SERVICE_UNITS[0]
    states = (
        ServiceStatus(unit, installed=False, active="inactive", sub="dead").state,
        ServiceStatus(unit, installed=True, active="active", sub="running").state,
        ServiceStatus(unit, installed=True, active="failed", sub="").state,
    )
    assert states == ("not installed", "active (running)", "failed")


def test_the_seven_units_of_the_spec_are_listed_in_order() -> None:
    """Services: hermes, bot, health, daily, backup, archive check, tax pack."""
    assert [unit.name for unit in SERVICE_UNITS] == [
        "hermes",
        "bot",
        "health",
        "daily",
        "backup",
        "archive check",
        "tax pack",
    ]


def test_actions_are_bootstrap_restart_rollback_and_a_read_only_diagnosis() -> None:
    """MVP1 shows these as commands to copy; the diagnosis is hermes status."""
    assert ([action.label for action in VPS_ACTIONS], DIAGNOSIS.command) == (
        ["bootstrap", "restart", "rollback"],
        "hermes status",
    )


def test_age_text_steps_at_the_minute_hour_and_day_boundaries() -> None:
    """59 s is just now, 60 s one minute, 3600 s one hour, 86400 s one day."""
    ages = [age_text(_NOW - timedelta(seconds=s), _NOW) for s in (59, 60, 3600, 86400)]
    assert ages == ["just now", "1 min ago", "1 h ago", "1 d ago"]


@given(st.integers(min_value=1, max_value=10**8))
def test_age_text_never_says_ago_for_the_future(seconds: int) -> None:
    """A time after now (clock skew) reads "in the future", never a negative age."""
    assert age_text(_NOW + timedelta(seconds=seconds), _NOW) == "in the future"
