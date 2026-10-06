"""The Shadow test screen computed from records, with no I/O (issue #718).

Under ADR-021 §2 a shadow run counts only from day 0, the first bar after
every Ready rule holds. ``hermes shadow-start`` (issue #797) records day 0 as a
JSON file next to the ledger; until that record exists the run is a warm-up
whose days do not count. Which fact starts the 90 days is chosen in one place,
:data:`DAY_ZERO_SOURCE`, so a later ruling changes one line.

The certification checks are mechanical: each is pass, fail or pending, never a
number. A fail stands as soon as its fact is recorded (a missed day, a kill, a
logged §11 deviation); a pass stands only once it can no longer change (day 90
reached, or the closing notebook entry signed). Nothing here reads P&L, equity
or a price, so the screen has no money for ``pnl_visible`` to hide.

Reference: openspec/changes/718-shadow-screen/change.md; ADR-021 §2 and §3;
docs/experiments/swing-mvp1/success-criteria.md (G8 ``SHADOW_CERTIFIED``).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from hermes.application.console.shadow_figures import SHADOW_TEST_DAYS
from hermes.domain.strategy.swing.adapter import swing_parameters

__all__ = [
    "CHECK_DAYS",
    "CHECK_KILL_CLEAN",
    "CHECK_KILL_FIRED",
    "CHECK_NOTEBOOK",
    "CHECK_SLIPPAGE",
    "DAY_ZERO_SOURCE",
    "MAX_SLIPPAGE_BPS",
    "MECHANICAL_NOTE",
    "CertificationCheck",
    "CheckState",
    "DayZeroSource",
    "NotebookEntry",
    "NotebookSource",
    "ReadyRule",
    "ReadyVerdict",
    "ShadowEvidence",
    "ShadowEvidenceSource",
    "ShadowRecordError",
    "ShadowRunRecord",
    "ShadowRunRecords",
    "ShadowScreen",
    "ShadowSources",
    "build_shadow_screen",
    "day_zero",
    "evaluate_checks",
    "frozen_settings",
    "missed_days",
    "parse_notebook",
    "run_progress",
    "run_words",
    "shadow_day",
    "sources_from",
    "warmup_days",
]

MAX_SLIPPAGE_BPS: Final = Decimal(30)
"""M-02: the median sim-vs-live drift a certified run stays within (ADR-021 §2 Done 3)."""

MECHANICAL_NOTE: Final = "mechanical: pass or fail stands"

CHECK_DAYS: Final = "90 days in a row"
CHECK_SLIPPAGE: Final = "M-02 slippage median ≤ 30 bps"
CHECK_KILL_FIRED: Final = "no kill trigger fired"
CHECK_KILL_CLEAN: Final = "kill-trigger check clean"
CHECK_NOTEBOOK: Final = "lab notebook signed"

_NO_DAY_ZERO: Final = "no day 0 yet"
_SHADOW_PHASE: Final = "shadow"
_OPERATOR: Final = "operator"
_ENTRIES_HEADING: Final = re.compile(r"^## Entries[ \t]*$", re.MULTILINE)
_ENTRY_HEADER: Final = re.compile(r"^### (\S+) \| (\S+) \| (\S+) \| (.+?)\s*$")


class ShadowRecordError(ValueError):
    """A day-0 record exists but cannot be read as one."""


class CheckState(StrEnum):
    """The only three answers a certification check gives."""

    PASS = "pass"  # noqa: S105 - a verdict word, not a secret
    FAIL = "fail"
    PENDING = "pending"


class ReadyVerdict(StrEnum):
    """A Ready rule's verdict as ``hermes shadow-start`` records it (issue #797)."""

    PASS = "pass"  # noqa: S105 - a verdict word, not a secret
    FAIL = "fail"
    BY_HAND = "confirm by hand"


@dataclass(frozen=True, slots=True)
class ReadyRule:
    """One ADR-021 §2 Ready rule and its verdict at day 0.

    Attributes:
        rule: The rule's name as recorded.
        verdict: Pass, fail or confirm by hand.
    """

    rule: str
    verdict: ReadyVerdict


@dataclass(frozen=True, slots=True)
class ShadowRunRecord:
    """The day-0 record of a counted shadow run (``hermes shadow-start``, issue #797).

    Attributes:
        run_id: The run's own id.
        strategy_id: The strategy the run trades.
        day_zero_at: When day 0 started (timezone-aware).
        release: The release running at day 0.
        ready_rules: The Ready rules and their verdicts at day 0.
    """

    run_id: str
    strategy_id: str
    day_zero_at: datetime
    release: str
    ready_rules: tuple[ReadyRule, ...] = ()


@runtime_checkable
class ShadowRunRecords(Protocol):
    """Read-only source of day-0 records."""

    def latest_run(self, strategy_id: str) -> ShadowRunRecord | None:
        """Return the newest day-0 record of ``strategy_id``, or ``None`` before day 0.

        Raises:
            ShadowRecordError: A record exists but cannot be read.
        """
        ...


@dataclass(frozen=True, slots=True)
class NotebookEntry:
    """One lab-notebook entry header (``### time | phase | actor | action``).

    Attributes:
        at: Entry time.
        phase: ``shadow``, ``train``, ``pre-registration`` …
        actor: ``operator``, ``engine`` …
        action: The entry's one-line action.
    """

    at: datetime
    phase: str
    actor: str
    action: str

    @property
    def logs_deviation(self) -> bool:
        """Return whether the entry records a §11 deviation (not one ruled out)."""
        action = self.action.lower()
        return "deviation" in action and "not a deviation" not in action


@runtime_checkable
class NotebookSource(Protocol):
    """Read-only source of the lab notebook."""

    def notebook_entries(self) -> tuple[NotebookEntry, ...] | None:
        """Return the entries oldest first, or ``None`` when the notebook is not here."""
        ...


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    """Ledger facts the checks read; never a money figure.

    Attributes:
        seeded_at: The earliest capital seed, if any.
        first_fill_at: The first fill at or after the seed, if any.
        missed_days: Local days after day 0 and before today with no bar check.
        live_drifts_bps: Sim-vs-live drift of every live-book fill since day 0.
        kill_fired: A kill (strategy pause) was recorded since day 0.
        kill_check_clean: The recorded kill-trigger check verdict since day 0,
            ``None`` while ``hermes serve`` does not record one (issue #796).
    """

    seeded_at: datetime | None = None
    first_fill_at: datetime | None = None
    missed_days: tuple[date, ...] = ()
    live_drifts_bps: tuple[Decimal, ...] = ()
    kill_fired: bool = False
    kill_check_clean: bool | None = None


@runtime_checkable
class ShadowEvidenceSource(Protocol):
    """Read-only source of the shadow evidence."""

    def shadow_evidence(self, since: datetime | None) -> ShadowEvidence:
        """Return the seed and first fill, plus the facts since ``since`` when given."""
        ...


@dataclass(frozen=True, slots=True)
class ShadowSources:
    """Where the Shadow test screen reads from; every source is read-only.

    Attributes:
        evidence: The ledger facts (seed, first fill, checks since day 0).
        runs: The day-0 records (``hermes shadow-start``, issue #797).
        notebook: The lab notebook.
        strategy_id: The roster's strategy, whose day-0 record counts.
    """

    evidence: ShadowEvidenceSource | None = None
    runs: ShadowRunRecords | None = None
    notebook: NotebookSource | None = None
    strategy_id: str | None = None


class DayZeroSource(StrEnum):
    """Which fact starts the 90 counted days."""

    SHADOW_START_RECORD = "shadow-start record"
    FIRST_FILL = "first fill"
    SEED_DATE = "capital seed date"


DAY_ZERO_SOURCE: Final = DayZeroSource.SHADOW_START_RECORD
"""The one switch: ADR-021 §2-§3 put day 0 in the ``hermes shadow-start`` record."""


@dataclass(frozen=True, slots=True)
class CertificationCheck:
    """One certification check.

    Attributes:
        name: What is checked.
        state: Pass, fail or pending.
        detail: Short words on why; never a number on a gated metric.
    """

    name: str
    state: CheckState
    detail: str


def day_zero(
    record: ShadowRunRecord | None,
    evidence: ShadowEvidence,
    *,
    source: DayZeroSource = DAY_ZERO_SOURCE,
) -> datetime | None:
    """Return when the counted run started, by the chosen source.

    Args:
        record: The newest day-0 record, if any.
        evidence: The ledger facts (seed and first fill).
        source: Which fact counts; :data:`DAY_ZERO_SOURCE` by default.

    Returns:
        The start, or ``None`` while the run is a warm-up.
    """
    match source:
        case DayZeroSource.SHADOW_START_RECORD:
            return None if record is None else record.day_zero_at
        case DayZeroSource.FIRST_FILL:
            return evidence.first_fill_at
        case DayZeroSource.SEED_DATE:
            return evidence.seeded_at


def shadow_day(day_zero_at: datetime, as_of: datetime, tz: tzinfo) -> int:
    """Return the counted day: 0 on day 0's date, then one more per local day.

    Args:
        day_zero_at: When day 0 started.
        as_of: Read time.
        tz: Timezone whose calendar days are counted.

    Returns:
        Whole local days since day 0, never below 0.
    """
    return max((as_of.astimezone(tz).date() - day_zero_at.astimezone(tz).date()).days, 0)


def warmup_days(seeded_at: datetime, as_of: datetime, tz: tzinfo) -> int:
    """Return the warm-up day since the seed, the seed's own day being day 1.

    Args:
        seeded_at: The capital seed.
        as_of: Read time.
        tz: Timezone whose calendar days are counted.

    Returns:
        The day number, at least 1.
    """
    return max((as_of.astimezone(tz).date() - seeded_at.astimezone(tz).date()).days + 1, 1)


def run_progress(day: int, length_days: int = SHADOW_TEST_DAYS) -> float:
    """Return the share of the run done, clamped to [0, 1].

    Args:
        day: The counted day.
        length_days: The run's length.

    Returns:
        ``day / length_days``, 0 before day 0 and 1 from the last day on.
    """
    if length_days <= 0:
        return 0.0
    return min(max(day, 0), length_days) / length_days


def missed_days(
    checked_at: Iterable[datetime], day_zero_at: datetime, as_of: datetime, tz: tzinfo
) -> tuple[date, ...]:
    """Return the local days strictly between day 0 and today with no bar check.

    Day 0's own date and today are left out: neither is a whole day of checks.

    Args:
        checked_at: When each bar check since day 0 ran.
        day_zero_at: When day 0 started.
        as_of: Read time.
        tz: Timezone whose calendar days are counted.

    Returns:
        The missed days, oldest first.
    """
    checked = {at.astimezone(tz).date() for at in checked_at}
    start = day_zero_at.astimezone(tz).date()
    today = as_of.astimezone(tz).date()
    span = (today - start).days
    days = (start + timedelta(days=offset) for offset in range(1, max(span, 0)))
    return tuple(day for day in days if day not in checked)


def _pending(name: str, detail: str) -> CertificationCheck:
    return CertificationCheck(name, CheckState.PENDING, detail)


def _days_check(day: int, evidence: ShadowEvidence, length: int) -> CertificationCheck:
    if evidence.missed_days:
        first = evidence.missed_days[0]
        return CertificationCheck(CHECK_DAYS, CheckState.FAIL, f"no bar check on {first:%d %b}")
    if day >= length:
        return CertificationCheck(CHECK_DAYS, CheckState.PASS, f"{length} days, none missed")
    return _pending(CHECK_DAYS, f"day {day} of {length}, none missed")


def _slippage_check(day: int, evidence: ShadowEvidence, length: int) -> CertificationCheck:
    drifts = evidence.live_drifts_bps
    if not drifts:
        return _pending(CHECK_SLIPPAGE, "not enough fills")
    if day < length:
        return _pending(CHECK_SLIPPAGE, f"decided at day {length}")
    if statistics.median(drifts) <= MAX_SLIPPAGE_BPS:
        return CertificationCheck(CHECK_SLIPPAGE, CheckState.PASS, "median within the limit")
    return CertificationCheck(CHECK_SLIPPAGE, CheckState.FAIL, "median above the limit")


def _kill_fired_check(day: int, evidence: ShadowEvidence, length: int) -> CertificationCheck:
    if evidence.kill_fired:
        return CertificationCheck(CHECK_KILL_FIRED, CheckState.FAIL, "a kill trigger fired")
    if day >= length:
        return CertificationCheck(CHECK_KILL_FIRED, CheckState.PASS, "none fired")
    return _pending(CHECK_KILL_FIRED, "none so far")


def _kill_clean_check(day: int, evidence: ShadowEvidence, length: int) -> CertificationCheck:
    clean = evidence.kill_check_clean
    if clean is None:
        return _pending(CHECK_KILL_CLEAN, "not run by serve yet · issue #796")
    if not clean:
        return CertificationCheck(CHECK_KILL_CLEAN, CheckState.FAIL, "the check reported a breach")
    if day >= length:
        return CertificationCheck(CHECK_KILL_CLEAN, CheckState.PASS, "clean")
    return _pending(CHECK_KILL_CLEAN, "clean so far")


def _notebook_check(
    day_zero_at: datetime, entries: tuple[NotebookEntry, ...] | None, length: int
) -> CertificationCheck:
    if entries is None:
        return _pending(CHECK_NOTEBOOK, "notebook not available here")
    shadow = [e for e in entries if e.phase == _SHADOW_PHASE and e.at >= day_zero_at]
    deviation = next((e for e in shadow if e.logs_deviation), None)
    if deviation is not None:
        return CertificationCheck(
            CHECK_NOTEBOOK, CheckState.FAIL, f"§11 deviation logged {deviation.at:%d %b}"
        )
    closing_from = day_zero_at + timedelta(days=length)
    closing = next(
        (e for e in shadow if e.actor == _OPERATOR and e.at >= closing_from),
        None,
    )
    if closing is not None:
        return CertificationCheck(
            CHECK_NOTEBOOK, CheckState.PASS, f"closing entry signed {closing.at:%d %b}"
        )
    return _pending(CHECK_NOTEBOOK, "closing entry not signed yet")


def evaluate_checks(
    *,
    day: int | None,
    day_zero_at: datetime | None,
    evidence: ShadowEvidence,
    entries: tuple[NotebookEntry, ...] | None,
    length_days: int = SHADOW_TEST_DAYS,
) -> tuple[CertificationCheck, ...]:
    """Return the five certification checks, in the screen's order.

    Args:
        day: The counted day, ``None`` during the warm-up.
        day_zero_at: When day 0 started, ``None`` during the warm-up.
        evidence: The ledger facts since day 0.
        entries: The lab notebook, ``None`` when it is not here.
        length_days: The run's length.

    Returns:
        Days in a row, M-02 slippage, no kill fired, kill check clean,
        notebook signed. During the warm-up every check is pending.
    """
    if day is None or day_zero_at is None:
        names = (CHECK_DAYS, CHECK_SLIPPAGE, CHECK_KILL_FIRED, CHECK_KILL_CLEAN, CHECK_NOTEBOOK)
        return tuple(_pending(name, _NO_DAY_ZERO) for name in names)
    return (
        _days_check(day, evidence, length_days),
        _slippage_check(day, evidence, length_days),
        _kill_fired_check(day, evidence, length_days),
        _kill_clean_check(day, evidence, length_days),
        _notebook_check(day_zero_at, entries, length_days),
    )


def frozen_settings(parameters: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    """Return the pre-registered parameters ``hermes serve`` trades with, in order.

    Args:
        parameters: The mapping to show; :func:`swing_parameters` (the source
            serve and the certification fingerprint read) when ``None``.

    Returns:
        ``(name, value)`` pairs.
    """
    source = swing_parameters() if parameters is None else parameters
    return tuple(source.items())


def parse_notebook(text: str) -> tuple[NotebookEntry, ...]:
    """Return the entry headers of a lab notebook, oldest first.

    Only lines after ``## Entries`` count, so the format template in the
    preamble is never read as an entry; a header whose time does not parse is
    skipped.

    Args:
        text: The notebook's Markdown.

    Returns:
        The entries in file order.
    """
    heading = _ENTRIES_HEADING.search(text)
    if heading is None:
        return ()
    body = text[heading.end() :]
    entries: list[NotebookEntry] = []
    for line in body.splitlines():
        match = _ENTRY_HEADER.match(line)
        if match is None:
            continue
        stamp, phase, actor, action = match.groups()
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if at.tzinfo is None:
            continue
        entries.append(NotebookEntry(at=at, phase=phase, actor=actor, action=action))
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class ShadowScreen:
    """Everything the Shadow test screen shows.

    Attributes:
        source: Which fact starts the count.
        day_zero_at: When day 0 started, ``None`` during the warm-up.
        day: The counted day, ``None`` during the warm-up.
        seeded_at: The capital seed, if any.
        warmup_day: Days since the seed (the seed's day is 1), ``None`` without a seed.
        record: The newest day-0 record, if any.
        checks: The five certification checks.
        settings: The frozen pre-registered parameters.
        notebook: The lab notebook entries, ``None`` when not here.
        length_days: The run's length.
    """

    source: DayZeroSource
    day_zero_at: datetime | None
    day: int | None
    seeded_at: datetime | None
    warmup_day: int | None
    record: ShadowRunRecord | None
    checks: tuple[CertificationCheck, ...]
    settings: tuple[tuple[str, str], ...]
    notebook: tuple[NotebookEntry, ...] | None
    length_days: int = SHADOW_TEST_DAYS

    @property
    def counting(self) -> bool:
        """Return whether the run's days count (day 0 is known)."""
        return self.day is not None

    @property
    def progress(self) -> float:
        """Return the share of the run done; 0 during the warm-up."""
        return 0.0 if self.day is None else run_progress(self.day, self.length_days)


def sources_from(model: object, strategy_id: str = "") -> ShadowSources:
    """Return the shadow sources ``model`` itself serves.

    A demo scenario serves all three; the ledger read model serves only the
    evidence (its day-0 records and notebook are files, see the console's
    composition root).

    Args:
        model: Any read model.
        strategy_id: Whose day-0 record counts.

    Returns:
        The sources the model implements; the rest ``None``.
    """
    return ShadowSources(
        evidence=model if isinstance(model, ShadowEvidenceSource) else None,
        runs=model if isinstance(model, ShadowRunRecords) else None,
        notebook=model if isinstance(model, NotebookSource) else None,
        strategy_id=strategy_id,
    )


def run_words(screen: ShadowScreen) -> str:
    """Return the bottom 90-day line's words, counted from the same day 0 as tab 5.

    Args:
        screen: The Shadow test screen.

    Returns:
        ``Shadow test · warm-up · not counting`` without a day 0;
        ``Shadow test · day 12 of 90`` while counting, with ``· run complete``
        from day 90 on.
    """
    if screen.day is None:
        return "Shadow test · warm-up · not counting"
    day = min(screen.day, screen.length_days)
    words = f"Shadow test · day {day} of {screen.length_days}"
    return words + " · run complete" if screen.day >= screen.length_days else words


def build_shadow_screen(
    *,
    record: ShadowRunRecord | None,
    evidence: ShadowEvidenceSource | None,
    notebook: tuple[NotebookEntry, ...] | None,
    as_of: datetime,
    tz: tzinfo,
    source: DayZeroSource = DAY_ZERO_SOURCE,
    parameters: Mapping[str, str] | None = None,
) -> ShadowScreen:
    """Return the Shadow test screen.

    Args:
        record: The newest day-0 record, if any.
        evidence: Where the ledger facts come from; ``None`` reads none.
        notebook: The lab notebook entries, ``None`` when not here.
        as_of: Read time.
        tz: Timezone whose calendar days are counted.
        source: Which fact starts the count (:data:`DAY_ZERO_SOURCE`).
        parameters: Frozen settings override (tests); the serve source otherwise.

    Returns:
        The screen; a warm-up while day 0 is unknown.

    Raises:
        LedgerError: The evidence source could not read the ledger.
    """
    base = ShadowEvidence() if evidence is None else evidence.shadow_evidence(None)
    start = day_zero(record, base, source=source)
    facts = base
    if start is not None and evidence is not None:
        facts = evidence.shadow_evidence(start)
    day = None if start is None else shadow_day(start, as_of, tz)
    seeded = base.seeded_at
    return ShadowScreen(
        source=source,
        day_zero_at=start,
        day=day,
        seeded_at=seeded,
        warmup_day=None if seeded is None else warmup_days(seeded, as_of, tz),
        record=record,
        checks=evaluate_checks(day=day, day_zero_at=start, evidence=facts, entries=notebook),
        settings=frozen_settings(parameters),
        notebook=notebook,
    )
