"""What the console's Lab screen shows: the backtested strategies and how they fared.

The Lab screen (issue #1056) is read-only. It reads only committed files under
``docs/experiments/<experiment>/``:

- ``batches/*.md``: a research batch's front matter, with each candidate's
  family, ``summary`` and signed parameters (ADR-026 §1);
- ``pre-registration-v*.md``: the front matter of a single pre-registration;
- ``cards/<strategy-id>.md``: the hypothesis card (rules, expected sign,
  falsification test);
- ``trial-log.jsonl``: one line per trial, hash-chained (ADR-012 D-11);
- ``verdicts/<batch-id>/<strategy-id>.<stage>.json``: the PASS, FAIL or NA of
  every check, with no metric value (ADR-026 §6).

:func:`build_lab` joins them into one :class:`LabStrategy` per strategy id,
including a pre-registered strategy that has no run yet. The verdict records
carry no values, so the screen's default render shows none. The only numbers
that come from outside them are the trial log's own, kept in
:attr:`RunRow.values` and read only when the caller asked for them
(:func:`values_allowed`): never in an AI session (ADR-012 D-06).

Reference: openspec/changes/1056-lab-screen/change.md.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol

import yaml

__all__ = [
    "AI_SESSION_VARS",
    "FAMILY_VALIDATION_BUDGET",
    "GATE_CHECKS",
    "LabFiles",
    "LabReader",
    "LabSnapshot",
    "LabStatus",
    "LabStrategy",
    "Mark",
    "ParameterRow",
    "RunRow",
    "VerdictCheck",
    "build_lab",
    "card_sections",
    "check_label",
    "front_matter",
    "values_allowed",
]

FAMILY_VALIDATION_BUDGET: Final = 5
"""Validation touches a family may spend (ADR-021 §5)."""

AI_SESSION_VARS: Final = ("HERMES_AI_SESSION", "CLAUDECODE")
"""An environment carrying either is an AI session; it never sees a value."""

GATE_CHECKS: Final = (
    "max_drawdown",
    "recovery_days",
    "profit_factor",
    "ulcer_index",
    "fee_share",
    "win_rate",
    "dsr",
    "pbo",
    "t_stat",
    "trade_count",
    "cost_stress",
    "positive_years",
    "bull_return",
    "bear_return",
    "validation_vs_train",
    "neighbours",
    "correlation",
)
"""The gate's 17 checks in the fixed order the screen lists them."""

_CHECK_ALIASES: Final = {"fees_to_gross_pnl": "fee_share"}
_SECTION9: Final = {
    "max_drawdown": "< 15%",
    "recovery_days": "< 90 days from a drawdown ≥ 8%",
    "profit_factor": "> 1.3",
    "ulcer_index": "< 10",
    "fee_share": "< 30% of gross P&L",
    "win_rate": "≥ 30%",
}
"""Pre-registration §9 secondary thresholds, unchanged since v1 (v2 §9, ADR-026 §5)."""

_LEGACY_FAMILIES: Final = {
    "swing-donchian-4h": "donchian-breakout-4h",
    "swing-mvp1-v2": "donchian-breakout-4h",
}
"""v1 and v2 predate the ``idea_family`` field; ADR-026 §1 puts both in this family."""

_STAGES: Final = ("train", "validation")
_FRONT_MATTER_FENCE: Final = "---"


class Mark(StrEnum):
    """One check's or stage's outcome as the screen shows it."""

    PASS = "PASS"  # noqa: S105 - a check outcome, not a secret
    FAIL = "FAIL"
    NA = "NA"


class LabStatus(StrEnum):
    """Where a strategy stands in the funnel, strongest first.

    ``TRAIN_PASS`` passed Train and waits for Validation. ``NO_VERDICT`` has
    runs but no committed verdict record (it ran before the batch pipeline);
    ``DRAFT`` has no run at all.
    """

    CHAMPION = "CHAMPION"
    GATE_PASS = "GATE PASS"  # noqa: S105 - a status label, not a secret
    FAIL_VALIDATION = "FAIL · validation"
    FAIL_TRAIN = "FAIL · train"
    TRAIN_PASS = "TRAIN PASS"  # noqa: S105 - a status label, not a secret
    NO_VERDICT = "NO VERDICT"
    DRAFT = "DRAFT"


@dataclass(frozen=True, slots=True)
class VerdictCheck:
    """One check of a stage's verdict.

    Attributes:
        check: The check's name as the gate lists it.
        status: PASS, FAIL or NA.
    """

    check: str
    status: Mark


@dataclass(frozen=True, slots=True)
class StageVerdict:
    """A committed verdict record for one stage of one strategy.

    Attributes:
        stage: ``train`` or ``validation``.
        batch_id: The batch the verdict belongs to.
        run_id: The workflow run, or ``None`` when run locally.
        commit: The commit the run used, or ``None``.
        window: ``(start, end)`` ISO dates of the data window.
        trial_lines: Trial lines the run appended.
        checks: Every check's outcome, in the record's order.
        passed: The stage passed.
        goes_on: Set at Train: the candidate goes on to Validation.
        champion: Set at Validation: the candidate is the batch's champion.
    """

    stage: str
    batch_id: str
    run_id: int | None
    commit: str | None
    window: tuple[str, str] | None
    trial_lines: int
    checks: tuple[VerdictCheck, ...]
    passed: bool
    goes_on: bool
    champion: bool


@dataclass(frozen=True, slots=True)
class ParameterRow:
    """One free parameter: the signed value and what the runs used.

    Attributes:
        name: The parameter's name.
        base: The signed base value, or ``None`` when no signed value is recorded.
        neighbours: The signed neighbour values.
        as_run: Distinct values the trial lines used, first seen first.
        mismatch: A run used a value other than the signed base and every
            signed neighbour (or, with no signed value, none can be compared).
    """

    name: str
    base: str | None
    neighbours: tuple[str, ...]
    as_run: tuple[str, ...]
    mismatch: bool


@dataclass(frozen=True, slots=True)
class RunRow:
    """One trial line, as the RUNS table shows it.

    Attributes:
        at: When the trial was recorded (UTC).
        split: ``train`` or ``validation``.
        variant: ``base``, ``name=value`` for a neighbour, or ``cost x<N>``.
        window: ``(start, end)`` ISO dates.
        commit: The commit the run used (short).
        digest: The line's chain hash (short).
        chained: The line's ``prev_sha256`` is the previous line's hash.
        values: ``{label: text}`` of the trial's own figures, or ``None``
            when values were not asked for. Never filled in an AI session.
    """

    at: datetime
    split: str
    variant: str
    window: tuple[str, str]
    commit: str
    digest: str
    chained: bool
    values: Mapping[str, str] | None


@dataclass(frozen=True, slots=True)
class LabStrategy:
    """Everything the Lab screen shows about one strategy id.

    Attributes:
        strategy_id: The id.
        family: The idea family, or ``None`` when unknown.
        status: Where it stands in the funnel.
        signed: A signed batch or pre-registration covers it.
        brief: The batch ``summary`` lines (or the card's mechanism).
        rules: The card's ``Rules`` section, one entry per line.
        expects: The card's ``Expected sign`` lines.
        rejected_if: The card's ``Falsification test`` lines.
        parameters: Free parameters, signed against as-run.
        stages: ``(train, validation)`` marks: PASS, FAIL or ``None`` (no verdict).
        judged: ``"champion"``, ``"pass"``, ``"fail"`` or ``None`` (not judged yet).
        trials: Trial lines recorded for the id.
        family_touches: Validation touches the family has used.
        last_run: When the newest trial was recorded, or ``None``.
        batch_id: The batch it belongs to, or ``None``.
        document: Repo path of the batch or pre-registration that signs it.
        card: Repo path of its card, or ``None``.
        thresholds: ``{check: text}`` of the signed thresholds.
        verdicts: Committed verdicts, Train first.
        runs: Trial lines, newest first.
        notebook: Repo path of the lab notebook, or ``None``.
        validation_run: False when the Train verdict says ``goes_on: false``:
            Validation never ran, whatever its ``missing_evidence`` record says
            (the BH count covers the whole batch).
    """

    strategy_id: str
    family: str | None
    status: LabStatus
    signed: bool
    brief: tuple[str, ...]
    rules: tuple[str, ...]
    expects: tuple[str, ...]
    rejected_if: tuple[str, ...]
    parameters: tuple[ParameterRow, ...]
    stages: tuple[Mark | None, Mark | None]
    judged: str | None
    trials: int
    family_touches: int
    last_run: datetime | None
    batch_id: str | None
    document: str | None
    card: str | None
    thresholds: Mapping[str, str] = field(default_factory=dict)
    verdicts: tuple[StageVerdict, ...] = ()
    runs: tuple[RunRow, ...] = ()
    notebook: str | None = None
    validation_run: bool = True


@dataclass(frozen=True, slots=True)
class LabSnapshot:
    """The Lab screen's rows, read once.

    Attributes:
        strategies: One per strategy id, newest activity first.
        values_included: Trial figures were read into the runs.
    """

    strategies: tuple[LabStrategy, ...]
    values_included: bool


@dataclass(frozen=True, slots=True)
class LabFiles:
    """The committed files of one experiment, as text, keyed by repo path.

    Attributes:
        experiment: The experiment folder's name.
        batches: ``batches/*.md`` by path.
        pre_registrations: ``pre-registration-v*.md`` by path.
        cards: ``cards/*.md`` by path.
        trial_log: The ``trial-log.jsonl`` text, or ``""``.
        verdicts: ``verdicts/<batch>/<id>.<stage>.json`` by path.
        notebook: Repo path of the experiment's lab notebook, or ``None``.
    """

    experiment: str
    batches: Mapping[str, str] = field(default_factory=dict)
    pre_registrations: Mapping[str, str] = field(default_factory=dict)
    cards: Mapping[str, str] = field(default_factory=dict)
    trial_log: str = ""
    verdicts: Mapping[str, str] = field(default_factory=dict)
    notebook: str | None = None


class LabReader(Protocol):
    """Reads the committed experiment files; ``None`` when none are here."""

    def read(self) -> LabSnapshot | None:
        """Return the Lab's strategies, or ``None`` when no experiment is readable."""
        ...


def values_allowed(show_values: bool, env: Mapping[str, str]) -> bool:
    """Return whether the Lab may read and show the trial log's values.

    Args:
        show_values: ``show_values = true`` in the console settings file.
        env: The environment; an AI session carries ``HERMES_AI_SESSION`` or
            ``CLAUDECODE``.

    Returns:
        True only when the setting is on and no AI-session variable is set.
    """
    return show_values and not any(env.get(name) for name in AI_SESSION_VARS)


def front_matter(text: str) -> Mapping[str, object]:
    """Return a document's YAML front matter, or ``{}`` when it has none or is unreadable."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_FENCE:
        return {}
    for end in range(1, len(lines)):
        if lines[end].strip() == _FRONT_MATTER_FENCE:
            try:
                loaded = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:
                return {}
            return loaded if isinstance(loaded, dict) else {}
    return {}


def card_sections(text: str) -> Mapping[str, tuple[str, ...]]:
    """Return a card's ``## `` sections: lower-cased heading to its non-blank lines.

    Bullets lose their marker; table rows and separators are dropped (the
    parameters come from the batch, not from the card's table).
    """
    out: dict[str, list[str]] = {}
    heading: str | None = None
    body = text.split("\n---\n", 1)[1] if text.startswith("---") and "\n---\n" in text else text
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            out[heading] = []
        elif heading is not None and line and not line.startswith("|"):
            out[heading].append(line.removeprefix("- ").strip())
    return {key: tuple(value) for key, value in out.items()}


def check_label(name: str) -> str:
    """Return the gate's name for a verdict's check (``fees_to_gross_pnl`` is ``fee_share``)."""
    return _CHECK_ALIASES.get(name, name)


def _text(value: object) -> str:
    return str(value)


def _lines(value: object) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(value or "").splitlines() if line.strip())


def _parse_verdict(text: str) -> tuple[str, StageVerdict] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("schema") != "hermes.verdict/1":
        return None
    window = data.get("window")
    checks: list[VerdictCheck] = []
    for item in data.get("checks") or ():
        if not isinstance(item, dict):
            continue
        try:
            checks.append(VerdictCheck(check_label(str(item["check"])), Mark(item["status"])))
        except KeyError, ValueError:
            continue
    run_id = data.get("run_id")
    commit = data.get("commit")
    verdict = StageVerdict(
        stage=str(data.get("stage")),
        batch_id=str(data.get("batch_id")),
        run_id=run_id if isinstance(run_id, int) else None,
        commit=commit if isinstance(commit, str) else None,
        window=(
            (str(window["start"]), str(window["end"]))
            if isinstance(window, dict) and "start" in window and "end" in window
            else None
        ),
        trial_lines=int(data["trial_lines"]) if isinstance(data.get("trial_lines"), int) else 0,
        checks=tuple(checks),
        passed=data.get("passed") is True,
        goes_on=data.get("goes_on") is True,
        champion=data.get("champion") is True,
    )
    return str(data.get("strategy_id")), verdict


@dataclass(frozen=True, slots=True)
class _Trial:
    strategy_id: str
    at: datetime
    split: str
    window: tuple[str, str]
    commit: str
    digest: str
    chained: bool
    parameters: Mapping[str, object]
    figures: Mapping[str, object]


def _trials(text: str) -> tuple[_Trial, ...]:
    out: list[_Trial] = []
    previous: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
            at = datetime.fromisoformat(data["recorded_at"])
            digest = str(data["sha256"])
            out.append(
                _Trial(
                    strategy_id=str(data["strategy_id"]),
                    at=at,
                    split=str(data["split"]),
                    window=(str(data["window_start"])[:10], str(data["window_end"])[:10]),
                    commit=str(data.get("git_sha", ""))[:8],
                    digest=digest[:8],
                    chained=previous is None or data.get("prev_sha256") == previous,
                    parameters=data.get("parameters") or {},
                    figures={
                        **(data.get("metrics") or {}),
                        **({"sharpe": data["sharpe"]} if "sharpe" in data else {}),
                    },
                )
            )
            previous = digest
        except KeyError, ValueError, TypeError, json.JSONDecodeError:
            continue
    return tuple(out)


def _figure_text(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return _text(value)


@dataclass(slots=True)
class _Declared:
    strategy_id: str
    family: str | None = None
    signed: bool = False
    brief: tuple[str, ...] = ()
    parameters: dict[str, tuple[str | None, tuple[str, ...]]] = field(default_factory=dict)
    batch_id: str | None = None
    document: str | None = None
    card: str | None = None
    thresholds: dict[str, str] = field(default_factory=dict)


def _funnel_thresholds(block: object) -> dict[str, str]:
    if not isinstance(block, dict):
        return {}
    out: dict[str, str] = {}
    dsr = block.get("dsr_min", 0.95)
    out["dsr"] = f"≥ {dsr}"
    texts = {
        "pbo": ("pbo_max", "≤ {}"),
        "t_stat": ("t_stat_above", "> {}"),
        "trade_count": ("trade_count_min", "≥ {}"),
        "cost_stress": ("cost_stress_multiplier", "still positive at x{}"),
        "positive_years": ("positive_year_share_above", "share of years > {}"),
        "validation_vs_train": ("validation_to_train_return_min", "ratio ≥ {}"),
        "neighbours": ("neighbour_sharpe_ratio_min", "Sharpe ratio ≥ {}"),
        "correlation": ("return_correlation_below", "< {}"),
    }
    for check, (key, template) in texts.items():
        if key in block:
            out[check] = template.format(block[key])
    out["bull_return"] = "positive in every bull stretch"
    out["bear_return"] = "bounded in every bear stretch"
    return out


def _parameters(raw: object) -> dict[str, tuple[str | None, tuple[str, ...]]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str | None, tuple[str, ...]]] = {}
    for name, spec in raw.items():
        if isinstance(spec, dict):
            neighbours = spec.get("neighbours") or ()
            base = spec.get("base")
            out[str(name)] = (
                None if base is None else _text(base),
                tuple(_text(n) for n in neighbours) if isinstance(neighbours, list) else (),
            )
        else:
            out[str(name)] = (_text(spec), ())
    return out


def _declared(files: LabFiles) -> dict[str, _Declared]:
    out: dict[str, _Declared] = {}
    for path, text in files.pre_registrations.items():
        meta = front_matter(text)
        strategy = meta.get("strategy_id")
        if not isinstance(strategy, str):
            continue
        out[strategy] = _Declared(
            strategy_id=strategy,
            family=_text(meta["idea_family"]) if "idea_family" in meta else None,
            signed=meta.get("status") == "SIGNED",
            brief=_lines(meta.get("summary")),
            parameters=_parameters(meta.get("parameters")),
            document=path,
            thresholds=_funnel_thresholds(meta.get("backtest_funnel")),
        )
    for path, text in files.batches.items():
        meta = front_matter(text)
        candidates = meta.get("candidates")
        if not isinstance(candidates, list):
            continue
        thresholds = _funnel_thresholds(meta.get("backtest_funnel"))
        for item in candidates:
            if not isinstance(item, dict) or not isinstance(item.get("strategy_id"), str):
                continue
            strategy = item["strategy_id"]
            out[strategy] = _Declared(
                strategy_id=strategy,
                family=_text(item["idea_family"]) if "idea_family" in item else None,
                signed=meta.get("status") == "SIGNED",
                brief=_lines(item.get("summary")),
                parameters=_parameters(item.get("parameters")),
                batch_id=_text(meta["batch_id"]) if "batch_id" in meta else None,
                document=path,
                card=_text(item["card"]) if "card" in item else None,
                thresholds=thresholds,
            )
    return out


def _card_for(files: LabFiles, declared: _Declared | None, strategy: str) -> tuple[str | None, str]:
    if declared is not None and declared.card in files.cards:
        return declared.card, files.cards[declared.card or ""]
    for path, text in files.cards.items():
        if path.rsplit("/", 1)[-1].removesuffix(".md") == strategy:
            return path, text
    return None, ""


def _stage_mark(verdicts: Sequence[StageVerdict], stage: str) -> Mark | None:
    for verdict in reversed(verdicts):
        if verdict.stage == stage:
            return Mark.PASS if verdict.passed else Mark.FAIL
    return None


def _status(
    stages: tuple[Mark | None, Mark | None], verdicts: Sequence[StageVerdict], *, has_runs: bool
) -> LabStatus:
    champion = any(v.champion for v in verdicts if v.stage == "validation")
    train, validation = stages
    if champion:
        return LabStatus.CHAMPION
    if validation is Mark.PASS:
        return LabStatus.GATE_PASS
    if validation is Mark.FAIL:
        return LabStatus.FAIL_VALIDATION
    if train is Mark.FAIL:
        return LabStatus.FAIL_TRAIN
    if train is Mark.PASS:
        return LabStatus.TRAIN_PASS
    return LabStatus.NO_VERDICT if has_runs else LabStatus.DRAFT


def _judged(stages: tuple[Mark | None, Mark | None], status: LabStatus) -> str | None:
    if status is LabStatus.CHAMPION:
        return "champion"
    if status is LabStatus.GATE_PASS:
        return "pass"
    return "fail" if Mark.FAIL in stages else None


def _variant(trial: _Trial, signed: Mapping[str, tuple[str | None, tuple[str, ...]]]) -> str:
    multiplier = trial.parameters.get("cost_multiplier")
    if multiplier not in (None, 1, 1.0, "1", "1.0"):
        return f"cost x{multiplier}"
    for name, (base, _neighbours) in signed.items():
        if name in trial.parameters and base is not None and _text(trial.parameters[name]) != base:
            return f"{name}={_text(trial.parameters[name])}"
    return "base"


def _parameter_rows(
    signed: Mapping[str, tuple[str | None, tuple[str, ...]]], trials: Sequence[_Trial]
) -> tuple[ParameterRow, ...]:
    as_run: dict[str, list[str]] = {name: [] for name in signed}
    for trial in trials:
        for name, seen in as_run.items():
            if name in trial.parameters:
                value = _text(trial.parameters[name])
                if value not in seen:
                    seen.append(value)
    rows = []
    for name, (base, neighbours) in signed.items():
        runs = tuple(as_run[name])
        allowed = {base, *neighbours} if base is not None else set()
        rows.append(
            ParameterRow(
                name=name,
                base=base,
                neighbours=neighbours,
                as_run=runs,
                mismatch=bool(runs) and any(value not in allowed for value in runs),
            )
        )
    return tuple(rows)


def _touches(trials: Iterable[_Trial]) -> int:
    """Count distinct Validation touches: one per (strategy, batch, commit)."""
    return len(
        {
            (t.strategy_id, _text(t.parameters.get("batch.id", "-")), t.commit)
            for t in trials
            if t.split == "validation"
        }
    )


def build_lab(files: Sequence[LabFiles], *, include_values: bool) -> LabSnapshot:
    """Join the committed files of the experiments into one row per strategy id.

    Args:
        files: The experiments' files.
        include_values: Keep each trial's own figures in the runs. The caller
            decides with :func:`values_allowed`; when False the figures are
            dropped here, so nothing downstream can show them.

    Returns:
        One :class:`LabStrategy` per strategy id named by a batch, a
        pre-registration with a ``strategy_id``, a trial line or a verdict
        record, newest activity first (ids with no run last, by name).
    """
    strategies: list[LabStrategy] = []
    for experiment in files:
        declared = _declared(experiment)
        trials = _trials(experiment.trial_log)
        verdicts: dict[str, list[StageVerdict]] = {}
        for text in experiment.verdicts.values():
            parsed = _parse_verdict(text)
            if parsed is not None:
                verdicts.setdefault(parsed[0], []).append(parsed[1])
        ids = {*declared, *(t.strategy_id for t in trials), *verdicts}
        families = {
            s: (declared[s].family if s in declared and declared[s].family else None)
            or _LEGACY_FAMILIES.get(s)
            for s in ids
        }
        for strategy in ids:
            mine = [t for t in trials if t.strategy_id == strategy]
            card_path, card_text = _card_for(experiment, declared.get(strategy), strategy)
            sections = card_sections(card_text)
            plan = declared.get(strategy) or _Declared(strategy_id=strategy)
            ordered = sorted(verdicts.get(strategy, ()), key=lambda v: _STAGES.index(v.stage))
            validation_run = not any(v.stage == "train" and not v.goes_on for v in ordered)
            if not validation_run:
                ordered = [v for v in ordered if v.stage != "validation"]
            stages = (_stage_mark(ordered, "train"), _stage_mark(ordered, "validation"))
            status = _status(stages, ordered, has_runs=bool(mine))
            family = families[strategy] or (
                _text(front_matter(card_text)["idea_family"])
                if "idea_family" in front_matter(card_text)
                else None
            )
            family_ids = {s for s, f in families.items() if f is not None and f == family}
            runs = tuple(
                RunRow(
                    at=t.at,
                    split=t.split,
                    variant=_variant(t, plan.parameters),
                    window=t.window,
                    commit=t.commit,
                    digest=t.digest,
                    chained=t.chained,
                    values=(
                        {k: _figure_text(v) for k, v in t.figures.items()}
                        if include_values
                        else None
                    ),
                )
                for t in sorted(mine, key=lambda t: t.at, reverse=True)
            )
            strategies.append(
                LabStrategy(
                    strategy_id=strategy,
                    family=family,
                    status=status,
                    signed=plan.signed or bool(mine),
                    brief=plan.brief or sections.get("mechanism", ()),
                    rules=sections.get("rules", ()),
                    expects=sections.get("expected sign", ()),
                    rejected_if=sections.get("falsification test", ()),
                    parameters=_parameter_rows(plan.parameters, mine),
                    stages=stages,
                    judged=_judged(stages, status),
                    trials=len(mine),
                    family_touches=_touches(t for t in trials if t.strategy_id in family_ids),
                    last_run=max((t.at for t in mine), default=None),
                    batch_id=plan.batch_id,
                    document=plan.document,
                    card=card_path,
                    thresholds={**_SECTION9, **plan.thresholds},
                    verdicts=tuple(ordered),
                    runs=runs,
                    notebook=experiment.notebook,
                    validation_run=validation_run,
                )
            )
    strategies.sort(
        key=lambda s: (
            s.last_run is None,
            0.0 if s.last_run is None else -s.last_run.timestamp(),
            s.strategy_id,
        )
    )
    return LabSnapshot(strategies=tuple(strategies), values_included=include_values)
