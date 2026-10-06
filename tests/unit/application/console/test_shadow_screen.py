"""The Shadow test screen model: day 0, day N and the certification checks (issue #718).

Invariants:

- day 0 comes from one switch (``DAY_ZERO_SOURCE``), the ``hermes shadow-start``
  record by default (ADR-021 §2); without a record the run is a warm-up and
  nothing counts;
- day N is whole local days since day 0 (0 on day 0's date), never negative;
- the run bar's share is monotonic in the day and clamped to [0, 1];
- every check answers pass, fail or pending only: a fail stands as soon as its
  fact is recorded, a pass only once it can no longer change;
- the notebook template in the preamble is never read as an entry;
- the frozen settings are exactly what ``swing_parameters`` gives serve.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.shadow_screen import (
    CHECK_DAYS,
    CHECK_KILL_CLEAN,
    CHECK_KILL_FIRED,
    CHECK_NOTEBOOK,
    CHECK_SLIPPAGE,
    DAY_ZERO_SOURCE,
    CertificationCheck,
    CheckState,
    DayZeroSource,
    NotebookEntry,
    ShadowEvidence,
    ShadowRunRecord,
    build_shadow_screen,
    day_zero,
    evaluate_checks,
    frozen_settings,
    missed_days,
    parse_notebook,
    run_progress,
    run_words,
    shadow_day,
    sources_from,
    warmup_days,
)
from hermes.domain.strategy.swing.adapter import swing_parameters

_MADRID = ZoneInfo("Europe/Madrid")
_DAY0 = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
_SEED = datetime(2026, 10, 4, 8, tzinfo=UTC)
_FILL = datetime(2026, 10, 6, 16, 0, 5, tzinfo=UTC)
_RECORD = ShadowRunRecord(
    run_id="swing-donchian-4h-20261015T000000Z",
    strategy_id="swing-donchian-4h",
    day_zero_at=_DAY0,
    release="2026-10-14-abc1234",
)
_EVIDENCE = ShadowEvidence(seeded_at=_SEED, first_fill_at=_FILL)


def _check(
    name: str,
    *,
    day: int = 10,
    evidence: ShadowEvidence = _EVIDENCE,
    entries: tuple[NotebookEntry, ...] | None = (),
) -> CertificationCheck:
    checks = evaluate_checks(day=day, day_zero_at=_DAY0, evidence=evidence, entries=entries)
    return next(check for check in checks if check.name == name)


def _entry(at: datetime, action: str, *, actor: str = "operator") -> NotebookEntry:
    return NotebookEntry(at=at, phase="shadow", actor=actor, action=action)


# --- day 0 ---------------------------------------------------------------


def test_the_switch_defaults_to_the_shadow_start_record() -> None:
    """ADR-021 §2: the one switch reads day 0 from the hermes shadow-start record."""
    assert DAY_ZERO_SOURCE is DayZeroSource.SHADOW_START_RECORD


@pytest.mark.parametrize(
    ("source", "record", "expected"),
    [
        (DayZeroSource.SHADOW_START_RECORD, _RECORD, _DAY0),
        (DayZeroSource.SHADOW_START_RECORD, None, None),
        (DayZeroSource.FIRST_FILL, None, _FILL),
        (DayZeroSource.SEED_DATE, None, _SEED),
    ],
)
def test_day_zero_follows_the_chosen_source(
    source: DayZeroSource, record: ShadowRunRecord | None, expected: datetime | None
) -> None:
    """Each source names its own fact; the record source without a record is a warm-up."""
    assert day_zero(record, _EVIDENCE, source=source) == expected


# --- day N ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (_DAY0, 0),
        (_DAY0 + timedelta(hours=21, minutes=59), 0),
        (_DAY0 + timedelta(hours=22), 1),
        (_DAY0 + timedelta(days=45), 45),
        (_DAY0 + timedelta(days=95), 95),
        (_DAY0 - timedelta(days=3), 0),
    ],
    ids=["day-0", "same-local-day", "next-local-day", "mid-run", "past-90", "before-day-0"],
)
def test_shadow_day_counts_local_days_since_day_zero(as_of: datetime, expected: int) -> None:
    """Day 0 on its own Madrid date, one more per local day, never below 0, uncapped past 90."""
    assert shadow_day(_DAY0, as_of, _MADRID) == expected


def test_warmup_days_count_the_seed_day_as_one() -> None:
    """The warm-up keeps the old count: the seed's own local day is day 1."""
    assert (
        warmup_days(_SEED, _SEED, _MADRID),
        warmup_days(_SEED, _SEED + timedelta(days=3), _MADRID),
    ) == (1, 4)


@pytest.mark.parametrize(
    ("day", "expected"), [(-4, 0.0), (0, 0.0), (45, 0.5), (90, 1.0), (140, 1.0)]
)
def test_run_progress_is_the_clamped_share(day: int, expected: float) -> None:
    """The bar's share is day / 90, held at 0 before day 0 and at 1 from day 90."""
    assert run_progress(day) == expected


@given(st.integers(min_value=-30, max_value=200), st.integers(min_value=0, max_value=200))
def test_run_progress_is_monotonic_in_the_day(day: int, step: int) -> None:
    """A later day never draws a shorter bar, and the share stays within [0, 1]."""
    earlier, later = run_progress(day), run_progress(day + step)
    assert 0.0 <= earlier <= later <= 1.0


def test_missed_days_skip_day_zero_and_today() -> None:
    """Only whole days between day 0 and today count; a day without a check is missed."""
    as_of = _DAY0 + timedelta(days=4, hours=10)
    checks = [_DAY0 + timedelta(days=1, hours=4), _DAY0 + timedelta(days=3, hours=4)]
    assert missed_days(checks, _DAY0, as_of, _MADRID) == (date(2026, 10, 17),)


# --- the checks ----------------------------------------------------------


def test_warm_up_leaves_every_check_pending() -> None:
    """Before day 0 nothing is judged: all five checks are pending, in the screen's order."""
    checks = evaluate_checks(day=None, day_zero_at=None, evidence=_EVIDENCE, entries=())
    assert [(c.name, c.state, c.detail) for c in checks] == [
        (CHECK_DAYS, CheckState.PENDING, "no day 0 yet"),
        (CHECK_SLIPPAGE, CheckState.PENDING, "no day 0 yet"),
        (CHECK_KILL_FIRED, CheckState.PENDING, "no day 0 yet"),
        (CHECK_KILL_CLEAN, CheckState.PENDING, "no day 0 yet"),
        (CHECK_NOTEBOOK, CheckState.PENDING, "no day 0 yet"),
    ]


@pytest.mark.parametrize(
    ("day", "missed", "state", "detail"),
    [
        (10, (), CheckState.PENDING, "day 10 of 90, none missed"),
        (90, (), CheckState.PASS, "90 days, none missed"),
        (10, (date(2026, 10, 18),), CheckState.FAIL, "no bar check on 18 Oct"),
        (95, (date(2026, 10, 18),), CheckState.FAIL, "no bar check on 18 Oct"),
    ],
    ids=["pending", "pass", "fail-mid-run", "fail-stands-after-90"],
)
def test_days_in_a_row(day: int, missed: tuple[date, ...], state: CheckState, detail: str) -> None:
    """A missed day fails at once and for good; 90 days with none missed pass."""
    check = _check(CHECK_DAYS, day=day, evidence=ShadowEvidence(missed_days=missed))
    assert (check.state, check.detail) == (state, detail)


@pytest.mark.parametrize(
    ("day", "drifts", "state", "detail"),
    [
        (40, (), CheckState.PENDING, "not enough fills"),
        (40, ("12", "45"), CheckState.PENDING, "decided at day 90"),
        (90, ("4", "30", "60"), CheckState.PASS, "median within the limit"),
        (90, ("31", "29", "40"), CheckState.FAIL, "median above the limit"),
        (90, (), CheckState.PENDING, "not enough fills"),
    ],
    ids=["no-fills", "mid-run", "pass-at-the-limit", "fail", "no-fills-at-90"],
)
def test_m02_slippage_median(
    day: int, drifts: tuple[str, ...], state: CheckState, detail: str
) -> None:
    """M-02 is judged once at day 90 on the median live drift (≤ 30 bps passes); no number shows."""
    evidence = ShadowEvidence(live_drifts_bps=tuple(Decimal(d) for d in drifts))
    check = _check(CHECK_SLIPPAGE, day=day, evidence=evidence)
    assert (check.state, check.detail) == (state, detail)


@pytest.mark.parametrize(
    ("day", "fired", "state"),
    [(30, False, CheckState.PENDING), (90, False, CheckState.PASS), (30, True, CheckState.FAIL)],
)
def test_no_kill_trigger_fired(day: int, fired: bool, state: CheckState) -> None:
    """A recorded kill fails the run at once; none by day 90 passes."""
    check = _check(CHECK_KILL_FIRED, day=day, evidence=ShadowEvidence(kill_fired=fired))
    assert check.state is state


@pytest.mark.parametrize(
    ("day", "clean", "state", "detail"),
    [
        (30, None, CheckState.PENDING, "not run by serve yet · issue #796"),
        (30, True, CheckState.PENDING, "clean so far"),
        (90, True, CheckState.PASS, "clean"),
        (30, False, CheckState.FAIL, "the check reported a breach"),
    ],
)
def test_kill_trigger_check_clean(
    day: int, clean: bool | None, state: CheckState, detail: str
) -> None:
    """Unknown until serve records the check (issue #796); a breach fails, clean at 90 passes."""
    check = _check(CHECK_KILL_CLEAN, day=day, evidence=ShadowEvidence(kill_check_clean=clean))
    assert (check.state, check.detail) == (state, detail)


def test_notebook_signed_passes_on_a_closing_operator_entry() -> None:
    """An operator shadow entry from day 90 on is the closing signature."""
    entries = (_entry(_DAY0 + timedelta(days=91), "close the shadow run"),)
    check = _check(CHECK_NOTEBOOK, day=91, entries=entries)
    assert (check.state, check.detail) == (CheckState.PASS, "closing entry signed 14 Jan")


def test_notebook_logged_deviation_fails() -> None:
    """A §11 deviation logged after day 0 fails the check; 'not a deviation' does not."""
    entries = (
        _entry(_DAY0 + timedelta(days=2), "engine fix (not a deviation)"),
        _entry(_DAY0 + timedelta(days=5), "§11 deviation: fee table changed"),
    )
    check = _check(CHECK_NOTEBOOK, day=10, entries=entries)
    assert (check.state, check.detail) == (CheckState.FAIL, "§11 deviation logged 20 Oct")


_ENGINE_CLOSE = NotebookEntry(
    at=_DAY0 + timedelta(days=91), phase="shadow", actor="engine", action="close"
)


@pytest.mark.parametrize(
    ("entries", "detail"),
    [
        (None, "notebook not available here"),
        ((), "closing entry not signed yet"),
        (
            (_entry(_DAY0 + timedelta(days=91), "close", actor="engine"),),
            "closing entry not signed yet",
        ),
    ],
    ids=["missing", "empty", "engine-is-not-a-signature"],
)
def test_notebook_pending(entries: tuple[NotebookEntry, ...] | None, detail: str) -> None:
    """No notebook, no closing entry, or one not by the operator leaves the check pending."""
    check = _check(CHECK_NOTEBOOK, day=91, entries=entries)
    assert (check.state, check.detail) == (CheckState.PENDING, detail)


@given(
    st.integers(min_value=0, max_value=200),
    st.lists(st.decimals(min_value=-100, max_value=100, places=2), max_size=5),
    st.booleans(),
    st.sampled_from([None, True, False]),
)
def test_every_check_answers_only_pass_fail_or_pending(
    day: int, drifts: list[Decimal], fired: bool, clean: bool | None
) -> None:
    """Whatever the facts, each of the five checks is one of the three states, with no figure."""
    evidence = ShadowEvidence(
        live_drifts_bps=tuple(drifts), kill_fired=fired, kill_check_clean=clean
    )
    checks = evaluate_checks(day=day, day_zero_at=_DAY0, evidence=evidence, entries=())
    assert [c.name for c in checks] == [
        CHECK_DAYS,
        CHECK_SLIPPAGE,
        CHECK_KILL_FIRED,
        CHECK_KILL_CLEAN,
        CHECK_NOTEBOOK,
    ]
    assert {c.state for c in checks} <= {CheckState.PASS, CheckState.FAIL, CheckState.PENDING}
    assert [c.detail for c in checks if "bps" in c.detail] == []


# --- notebook and settings -----------------------------------------------


def test_parse_notebook_reads_entries_after_the_entries_heading_only() -> None:
    """The template header in the preamble is skipped; each entry keeps its four fields."""
    text = (
        "# Lab\n\n```text\n### YYYY-MM-DDTHH:MM:SSZ | phase | actor | action\n```\n\n"
        "## Entries\n\n### 2026-10-06T11:13:17Z | shadow | operator | shadow clock reset\n\n"
        "body\n\n### not-a-time | shadow | operator | skipped\n"
    )
    assert parse_notebook(text) == (
        NotebookEntry(
            at=datetime(2026, 10, 6, 11, 13, 17, tzinfo=UTC),
            phase="shadow",
            actor="operator",
            action="shadow clock reset",
        ),
    )


def test_frozen_settings_are_what_serve_trades_with() -> None:
    """The screen shows swing_parameters() itself, in order; nothing is a copy to drift."""
    assert frozen_settings() == tuple(swing_parameters().items())


# --- the screen ----------------------------------------------------------


class _Evidence:
    def __init__(self) -> None:
        self.asked: list[datetime | None] = []

    def shadow_evidence(self, since: datetime | None) -> ShadowEvidence:
        self.asked.append(since)
        return ShadowEvidence(seeded_at=_SEED, kill_fired=since is not None)


def test_screen_without_a_record_is_a_warm_up() -> None:
    """No day-0 record: no day, warm-up days from the seed, evidence read without a since."""
    evidence = _Evidence()
    screen = build_shadow_screen(
        record=None,
        evidence=evidence,
        notebook=None,
        as_of=_SEED + timedelta(days=6),
        tz=_MADRID,
    )
    assert (screen.counting, screen.day, screen.warmup_day, screen.progress, evidence.asked) == (
        False,
        None,
        7,
        0.0,
        [None],
    )


def test_screen_with_a_record_counts_from_its_day_zero() -> None:
    """A record makes day N count from day_zero_at and the checks read the facts since it."""
    evidence = _Evidence()
    screen = build_shadow_screen(
        record=_RECORD,
        evidence=evidence,
        notebook=(),
        as_of=_DAY0 + timedelta(days=12, hours=10),
        tz=_MADRID,
    )
    assert (screen.day, screen.day_zero_at, evidence.asked, screen.checks[2].state) == (
        12,
        _DAY0,
        [None, _DAY0],
        CheckState.FAIL,
    )


# --- the bottom 90-day line ----------------------------------------------


@pytest.mark.parametrize(
    ("record", "as_of", "words"),
    [
        (None, _DAY0 + timedelta(days=5), "Shadow test · warm-up · not counting"),
        (_RECORD, _DAY0 + timedelta(hours=1), "Shadow test · day 0 of 90"),
        (_RECORD, _DAY0 + timedelta(days=12, hours=10), "Shadow test · day 12 of 90"),
        (
            _RECORD,
            _DAY0 + timedelta(days=97, hours=10),
            "Shadow test · day 90 of 90 · run complete",
        ),
    ],
    ids=["warm-up", "day-0", "mid-run", "past-90"],
)
def test_run_words_count_from_the_same_day_zero(
    record: ShadowRunRecord | None, as_of: datetime, words: str
) -> None:
    """The bottom line's words come from the screen's own day 0: one source of truth."""
    screen = build_shadow_screen(
        record=record, evidence=None, notebook=None, as_of=as_of, tz=_MADRID
    )
    assert run_words(screen) == words


def test_sources_from_takes_only_what_the_model_serves() -> None:
    """A model with only shadow_evidence gives evidence and nothing else."""
    evidence = _Evidence()
    sources = sources_from(evidence)
    assert (sources.evidence, sources.runs, sources.notebook) == (evidence, None, None)
