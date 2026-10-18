"""The Lab screen's read model (issue #1056).

Invariants:

- one row per strategy id named by a batch, a pre-registration, a trial line
  or a verdict record; a pre-registration with no runs is a DRAFT row;
- the status comes from the committed verdict records alone (PASS, FAIL, NA);
- signed parameters are compared with the values the trial lines used, and a
  value that is neither the signed base nor a signed neighbour is a mismatch;
- the family's Validation touches are counted across every strategy in the
  family, one per (strategy, batch, commit);
- the trial log's figures are read only when asked for: without that the runs
  carry none, whatever the trial lines hold (ADR-012 D-06);
- values are allowed only with ``show_values`` on and no AI-session variable.

Every file here is synthetic; see :mod:`tests.support.lab_files`.
"""

import json
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.lab import (
    GATE_CHECKS,
    LabFiles,
    LabStatus,
    Mark,
    build_lab,
    card_sections,
    check_label,
    front_matter,
    values_allowed,
)
from tests.support.lab_files import EXPERIMENT, FIGURES, synthetic_files


def _by_id(files: LabFiles | None = None, *, values: bool = False):
    snapshot = build_lab([files or synthetic_files()], include_values=values)
    return {s.strategy_id: s for s in snapshot.strategies}


def test_one_row_per_strategy_id_including_a_pre_registration_with_no_runs() -> None:
    """Batch candidates, a pre-registration, and a log-only id each get exactly one row."""
    assert sorted(_by_id()) == [
        "synth-daily-ma",
        "synth-ensemble",
        "synth-legacy",
        "synth-prereg-only",
        "synth-pullback",
        "synth-slope",
    ]


@pytest.mark.parametrize(
    ("strategy", "status", "stages"),
    [
        ("synth-ensemble", LabStatus.CHAMPION, (Mark.PASS, Mark.PASS)),
        ("synth-daily-ma", LabStatus.FAIL_VALIDATION, (Mark.PASS, Mark.FAIL)),
        ("synth-slope", LabStatus.FAIL_TRAIN, (Mark.FAIL, None)),
        ("synth-pullback", LabStatus.TRAIN_PASS, (Mark.PASS, None)),
        ("synth-legacy", LabStatus.NO_VERDICT, (None, None)),
        ("synth-prereg-only", LabStatus.DRAFT, (None, None)),
    ],
)
def test_status_follows_the_committed_verdicts(
    strategy: str, status: LabStatus, stages: tuple[Mark | None, Mark | None]
) -> None:
    """The status and the Train / Validation marks are the verdict records, nothing else."""
    row = _by_id()[strategy]
    assert (row.status, row.stages) == (status, stages)


def test_a_missing_evidence_validation_record_after_goes_on_false_is_not_run() -> None:
    """Train ``goes_on: false`` means Validation never ran, so its FAIL record is not a verdict."""
    by_id = _by_id()
    assert (
        by_id["synth-slope"].validation_run,
        by_id["synth-slope"].stages[1],
        [v.stage for v in by_id["synth-slope"].verdicts],
        by_id["synth-daily-ma"].validation_run,
        by_id["synth-daily-ma"].stages[1],
    ) == (False, None, ["train"], True, Mark.FAIL)


def test_only_the_champion_is_judged_a_champion() -> None:
    """A Validation PASS without the batch's champion flag is a gate pass, not a champion."""
    files = synthetic_files()
    key = f"docs/experiments/{EXPERIMENT}/verdicts/b1/synth-ensemble.validation.json"
    record = json.loads(files.verdicts[key])
    record["champion"] = False
    verdicts = {**files.verdicts, key: json.dumps(record)}
    row = _by_id(replace(files, verdicts=verdicts))["synth-ensemble"]
    assert (row.status, row.judged) == (LabStatus.GATE_PASS, "pass")


def test_a_parameter_run_off_the_signed_values_is_a_mismatch() -> None:
    """rsi_level 33 is neither the signed base 35 nor a neighbour: marked; the rest are not."""
    by_id = _by_id()
    pullback = {p.name: p for p in by_id["synth-pullback"].parameters}["rsi_level"]
    ensemble = [p.mismatch for p in by_id["synth-ensemble"].parameters]
    assert (pullback.base, pullback.as_run, pullback.mismatch, ensemble) == (
        "35",
        ("35", "33"),
        True,
        [False, False],
    )


def test_a_signed_neighbour_run_is_not_a_mismatch_and_is_named_by_its_variant() -> None:
    """agree_min 1 is a signed neighbour: no mark, and the run says which parameter moved."""
    runs = _by_id()["synth-ensemble"].runs
    assert sorted(r.variant for r in runs) == ["agree_min=1", "base", "base"]


def test_family_touches_count_validation_runs_across_the_family() -> None:
    """Each family used one Validation touch; a family with none shows 0."""
    by_id = _by_id()
    assert (
        by_id["synth-ensemble"].family_touches,
        by_id["synth-daily-ma"].family_touches,
        by_id["synth-slope"].family_touches,
    ) == (1, 1, 0)


def test_legacy_donchian_ids_share_one_family_budget() -> None:
    """v1 and v2 predate idea_family; ADR-026 puts both in donchian-breakout-4h."""
    log_lines = []
    for index, (strategy, split) in enumerate(
        (("swing-donchian-4h", "validation"), ("swing-mvp1-v2", "train"))
    ):
        log_lines.append(
            json.dumps(
                {
                    "strategy_id": strategy,
                    "split": split,
                    "window_start": "2020-09-01",
                    "window_end": "2024-06-30",
                    "git_sha": "f6883a10aa",
                    "recorded_at": f"2026-10-0{index + 1}T10:00:00+00:00",
                    "parameters": {},
                    "sha256": f"{index:064d}",
                }
            )
        )
    files = LabFiles(experiment="legacy", trial_log="\n".join(log_lines))
    rows = _by_id(files)
    assert [(s.family, s.family_touches) for s in rows.values()] == [
        ("donchian-breakout-4h", 1)
    ] * 2


def test_runs_carry_no_figure_unless_values_were_asked_for() -> None:
    """Without include_values every run's values are None, whatever the trial lines hold."""
    runs = [run for s in _by_id().values() for run in s.runs]
    assert {run.values for run in runs} == {None}


def test_runs_carry_the_figures_when_asked_for() -> None:
    """With include_values the run keeps its own sharpe, as text."""
    first = _by_id(values=True)["synth-ensemble"].runs[-1]
    assert first.values is not None
    assert first.values["sharpe"] == "1.739"


@given(st.lists(st.floats(min_value=-9, max_value=9, allow_nan=False), min_size=1, max_size=5))
def test_without_values_no_figure_text_survives_for_any_trial_line(figures: list[float]) -> None:
    """For any figures in the trial log, include_values=False leaves no run value anywhere."""
    lines = [
        json.dumps(
            {
                "strategy_id": "s",
                "split": "train",
                "window_start": "2020-09-01",
                "window_end": "2024-06-30",
                "git_sha": "abc",
                "recorded_at": f"2026-10-01T10:0{i}:00+00:00",
                "parameters": {},
                "sharpe": value,
                "metrics": {"dsr": value},
                "sha256": f"{i:064d}",
            }
        )
        for i, value in enumerate(figures)
    ]
    snapshot = build_lab(
        [LabFiles(experiment="e", trial_log="\n".join(lines))], include_values=False
    )
    runs = snapshot.strategies[0].runs
    assert (len(runs), {r.values for r in runs}, snapshot.values_included) == (
        len(figures),
        {None},
        False,
    )


def test_a_broken_hash_chain_is_flagged_on_the_line_after_the_break() -> None:
    """A line whose prev_sha256 is not the line before's hash is shown as unchained."""
    files = synthetic_files()
    lines = files.trial_log.splitlines()
    broken = json.loads(lines[1])
    broken["prev_sha256"] = "0" * 64
    lines[1] = json.dumps(broken)
    rows = _by_id(replace(files, trial_log="\n".join(lines) + "\n"))
    chained = {r.digest: r.chained for s in rows.values() for r in s.runs}
    assert list(chained.values()).count(False) == 1


def test_thresholds_come_from_the_signed_funnel_block_and_section_nine() -> None:
    """The batch's funnel numbers and the pre-registration §9 numbers show; none is invented."""
    thresholds = _by_id()["synth-ensemble"].thresholds
    assert (thresholds["pbo"], thresholds["t_stat"], thresholds["max_drawdown"]) == (
        "≤ 0.05",
        "> 3.0",
        "< 15%",
    )


def test_verdict_checks_use_the_gate_names() -> None:
    """The verdict's fees_to_gross_pnl is listed as the gate's fee_share."""
    checks = {c.check for v in _by_id()["synth-ensemble"].verdicts for c in v.checks}
    assert ("fee_share" in checks, "fees_to_gross_pnl" in checks, check_label("dsr")) == (
        True,
        False,
        "dsr",
    )


def test_every_gate_check_has_a_name_in_the_fixed_order() -> None:
    """The gate lists 17 checks, once each."""
    assert (len(GATE_CHECKS), len(set(GATE_CHECKS))) == (17, 17)


def test_a_verdict_with_another_schema_is_ignored() -> None:
    """Only hermes.verdict/1 records count; anything else leaves the row without a verdict."""
    files = synthetic_files()
    verdicts = {
        k: v.replace("hermes.verdict/1", "hermes.verdict/9") for k, v in files.verdicts.items()
    }
    rows = _by_id(replace(files, verdicts=verdicts))
    assert rows["synth-ensemble"].status is LabStatus.NO_VERDICT


def test_unreadable_files_leave_rows_not_errors() -> None:
    """Broken front matter, a broken verdict and a broken trial line are skipped, not raised."""
    files = LabFiles(
        experiment="e",
        batches={"b.md": "---\n: : :\n---\n"},
        verdicts={"v.json": "{not json"},
        trial_log="not json\n",
    )
    assert build_lab([files], include_values=False).strategies == ()


def test_front_matter_needs_the_fence() -> None:
    """A document without a leading --- fence has no front matter."""
    assert (front_matter("a: 1\n"), front_matter("---\na: 1\n---\n")) == ({}, {"a": 1})


def test_card_sections_keep_bullets_without_their_marker_and_drop_tables() -> None:
    """Sections by lower-cased heading; a table row is not a rule."""
    text = "---\nx: 1\n---\n## Rules\n\n- one\n- two\n| a | b |\n\n## Expected sign\nflat\n"
    assert card_sections(text) == {"rules": ("one", "two"), "expected sign": ("flat",)}


@pytest.mark.parametrize(
    ("show", "env", "allowed"),
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"CLAUDECODE": "1"}, False),
        (True, {"HERMES_AI_SESSION": "yes"}, False),
        (True, {"CLAUDECODE": ""}, True),
    ],
)
def test_values_need_the_setting_and_no_ai_session(
    show: bool, env: dict[str, str], allowed: bool
) -> None:
    """Values show only with show_values on and neither AI-session variable set."""
    assert values_allowed(show, env) is allowed


def test_figures_never_appear_in_a_row_built_without_them() -> None:
    """No distinctive figure of the synthetic trial log reaches any text of a strategy."""
    text = repr([s for s in build_lab([synthetic_files()], include_values=False).strategies])
    assert [figure for figure in FIGURES if figure in text] == []
