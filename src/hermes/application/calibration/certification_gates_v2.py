"""v2's gates: pre-registration-v2.md §4, §7, §9 and §10 for ``swing-mvp1-v2``.

v2 keeps v1's lifecycle and §9 secondary rows and changes the backtest gate to
DSR ≥ 0.95 plus the ADR-021 §5 funnel. :func:`parse_v2_backtest_rules` reads
the DSR floor from the signed v2 file's §10 text and the funnel thresholds
from its front matter; the secondary thresholds stay v1's, read from v1's
accessor, and the parser refuses a v2 file whose §9 no longer says they are
unchanged. :func:`evaluate_v2_gate` evaluates v1's backtest gate, replaces its
``dsr`` row and appends the funnel rows, so v1's gate code is untouched.

Out of ``BACKTEST_CERTIFIED`` the gate is v1's shadow gate with v2 §7's length
rule: at least 90 days and 30 round trips, whichever is later, capped at 180
days. :func:`parse_v2_shadow_rules` reads the three numbers from the signed §7
text. Every later gate is v1's, because v2 keeps v1's windows, lifecycle,
primary metrics and live kills; the same parser refuses a v2 file whose §4,
§7, §9 Primary or §10 shadow and live phase no longer says so.

Reference: pre-registration-v2.md §0, §4, §7, §9, §10; ADR-021 §5;
openspec/changes/v2-certify-gate/change.md;
openspec/changes/1021-v2-shadow-gate/change.md.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Final

from hermes.application.calibration.backtest_funnel import (
    FunnelEvidence,
    FunnelThresholds,
    evaluate_funnel,
    parse_funnel_front_matter,
)
from hermes.application.calibration.certification_gates import (
    CertificationRecord,
    GateCheck,
    GateEvidence,
    GateReport,
    evaluate_next_gate,
    parameter_fingerprint,
    v1_parameter_fingerprint,
)
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.execution.prereg_book import ExitPricing
from hermes.application.strategy_protocol.preregistration import PreRegistration

__all__ = [
    "V2_EXIT_PRICING",
    "V2_STRATEGY_ID",
    "V2BacktestRules",
    "V2RulesError",
    "V2ShadowRules",
    "evaluate_v2_gate",
    "parse_v2_backtest_rules",
    "parse_v2_shadow_rules",
    "v2_parameter_fingerprint",
]

V2_STRATEGY_ID: Final = "swing-mvp1-v2"
"""The strategy and experiment id pre-registration v2 signs."""

V2_EXIT_PRICING: Final = ExitPricing.BAR_CLOSE
"""The exit pricing v2 signed (§5.3, §6.2)."""

_DSR_FLOOR = re.compile(r"^- DSR < (\d+(?:\.\d+)?);$")
_SHADOW_LENGTH = re.compile(
    r"ends at (\d+) days and (\d+) round trips, whichever is later, capped at (\d+) days"
)
_UNCHANGED = "Unchanged from v1"
_NOT_COMPUTED = "not computed"


class V2RulesError(ValueError):
    """The v2 pre-registration does not state its gates as this module reads them."""


@dataclass(frozen=True, slots=True)
class V2BacktestRules:
    """What v2's backtest gate adds to v1's: the DSR floor and the funnel thresholds."""

    dsr_min: float
    """The backtest passes the DSR row at or above this (v2 §10 kills below it)."""

    funnel: FunnelThresholds
    """The ``backtest_funnel`` front matter block."""


@dataclass(frozen=True, slots=True)
class V2ShadowRules:
    """v2 §7's shadow length: the later of ``min_days`` and ``min_round_trips``, capped."""

    min_days: int
    """The shadow runs at least this many days."""

    min_round_trips: int
    """The shadow runs at least this many round trips, whichever comes later."""

    max_days: int
    """Reaching this many days before both minimums is inconclusive and fails."""


def _section(document: str, section: str) -> list[str]:
    """Lines under ``## <section>`` before its first ``###``, up to the next ``##``."""
    in_section = False
    body: list[str] = []
    for line in document.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(f"## {section}")
        elif line.startswith("### "):
            in_section = False
        elif in_section:
            body.append(line)
    return body


def _subsection(document: str, section: str, subsection: str) -> list[str]:
    """Lines under ``### <subsection>`` inside ``## <section>``, up to the next heading."""
    in_section = in_subsection = False
    body: list[str] = []
    for line in document.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(f"## {section}")
            in_subsection = False
        elif line.startswith("### "):
            in_subsection = in_section and line.startswith(f"### {subsection}")
        elif in_subsection:
            body.append(line)
    return body


def _starts_unchanged(lines: list[str]) -> bool:
    """Return True when the first non-blank line starts "Unchanged from v1"."""
    text = [line for line in lines if line.strip()]
    return bool(text) and text[0].startswith(_UNCHANGED)


def parse_v2_backtest_rules(document: str, front_matter: Mapping[str, object]) -> V2BacktestRules:
    """Return v2's backtest rules as the signed v2 pre-registration states them.

    Args:
        document: The full text of ``pre-registration-v2.md``.
        front_matter: Its parsed YAML front matter.

    Returns:
        The DSR floor from the §10 backtest phase and the funnel thresholds.

    Raises:
        V2RulesError: The document is for another experiment, its §10 backtest
            phase does not state exactly one ``- DSR < X;`` floor, or its §9
            secondary paragraph does not start "Unchanged from v1".
        FunnelThresholdError: The front matter is unsigned, names another
            strategy or has no usable ``backtest_funnel`` block.
    """
    experiment = front_matter.get("experiment_id")
    if experiment != V2_STRATEGY_ID:
        raise V2RulesError(f"experiment_id is {experiment!r}, not {V2_STRATEGY_ID!r}")
    funnel = parse_funnel_front_matter(front_matter, strategy_id=V2_STRATEGY_ID)

    if not _starts_unchanged(_subsection(document, "9.", "Secondary")):
        raise V2RulesError(f"§9 secondary metrics no longer say {_UNCHANGED!r}")

    floors = [
        float(match.group(1))
        for line in _subsection(document, "10.", "Backtest phase")
        if (match := _DSR_FLOOR.match(line.strip()))
    ]
    if len(floors) != 1:
        raise V2RulesError(f"§10 backtest phase states {len(floors)} DSR floors, not 1")
    return V2BacktestRules(dsr_min=floors[0], funnel=funnel)


def parse_v2_shadow_rules(document: str) -> V2ShadowRules:
    """Return v2's shadow length rule as the signed v2 pre-registration states it.

    Args:
        document: The full text of ``pre-registration-v2.md``.

    Returns:
        The days, round trips and cap from §7.

    Raises:
        V2RulesError: §4, §7, §9 Primary or §10 "Shadow phase and live phase"
            no longer starts "Unchanged from v1", so v1's later gates no
            longer describe v2; or §7 does not state exactly one length rule.
    """
    guards = (
        ("§4", _section(document, "4.")),
        ("§7", _section(document, "7.")),
        ("§9 primary metrics", _subsection(document, "9.", "Primary")),
        ("§10 shadow and live kills", _subsection(document, "10.", "Shadow phase and live phase")),
    )
    for name, lines in guards:
        if not _starts_unchanged(lines):
            raise V2RulesError(f"{name} no longer says {_UNCHANGED!r}")
    # The signed sentence wraps across lines, so match it on the joined text.
    text = " ".join(line.strip() for line in _section(document, "7."))
    rules = _SHADOW_LENGTH.findall(text)
    if len(rules) != 1:
        raise V2RulesError(f"§7 states {len(rules)} shadow length rules, not 1")
    days, round_trips, cap = rules[0]
    return V2ShadowRules(min_days=int(days), min_round_trips=int(round_trips), max_days=int(cap))


def v2_parameter_fingerprint(
    *, capital_budget: Decimal, universe: Sequence[str], exit_pricing: ExitPricing
) -> str:
    """Fingerprint what v2 trades with: v1's formula, plus its exit pricing when not signed.

    The signed bar-close exit leaves v1's formula unchanged, so a record
    certified before exit pricing was fingerprinted keeps its fingerprint;
    any other exit pricing makes another one (#1021 D-4).

    Args:
        capital_budget: The v2 roster entry's capital budget.
        universe: The followed pair symbols.
        exit_pricing: The v2 roster entry's exit pricing.

    Returns:
        Lower-case hex digest.
    """
    base = v1_parameter_fingerprint(capital_budget=capital_budget, universe=universe)
    if exit_pricing is V2_EXIT_PRICING:
        return base
    return parameter_fingerprint({"v1_formula": base, "exit_pricing": exit_pricing.value})


def _v2_dsr(row: GateCheck, dsr: float | None, floor: float) -> GateCheck:
    return replace(row, requirement=f">= {floor:g}", passed=dsr is not None and dsr >= floor)


def _funnel_rows(
    funnel: FunnelEvidence | None, thresholds: FunnelThresholds
) -> tuple[GateCheck, ...]:
    if funnel is None:
        return (
            GateCheck("funnel", "ADR-021 §5 funnel (--funnel-evidence)", "not computed", False),
        )
    return evaluate_funnel(funnel, thresholds)


def _length_rows(
    days_row: GateCheck, evidence: GateEvidence, rules: V2ShadowRules
) -> tuple[GateCheck, ...]:
    """v1's days row at v2's minimum, then v2's round-trip and cap rows."""
    days = evidence.shadow.days if evidence.shadow else None
    trips = evidence.shadow.round_trips if evidence.shadow else None
    return (
        replace(
            days_row,
            requirement=f">= {rules.min_days}",
            passed=days is not None and days >= rules.min_days,
        ),
        GateCheck(
            "shadow_round_trips",
            f">= {rules.min_round_trips}",
            _NOT_COMPUTED if trips is None else str(trips),
            trips is not None and trips >= rules.min_round_trips,
        ),
        GateCheck(
            "shadow_cap",
            f"<= {rules.max_days} days",
            _NOT_COMPUTED if days is None else str(days),
            days is not None and days <= rules.max_days,
        ),
    )


def evaluate_v2_gate(
    record: CertificationRecord,
    evidence: GateEvidence,
    *,
    now: datetime,
    v1: PreRegistration,
    rules: V2BacktestRules,
    shadow: V2ShadowRules,
    funnel: FunnelEvidence | None,
) -> GateReport:
    """Evaluate the gate out of a v2 record's current state.

    Pure, like :func:`evaluate_next_gate`. Out of ``UNTRUSTED`` it is v1's
    backtest gate with the ``dsr`` row at ``>= rules.dsr_min`` and the funnel
    rows appended. Out of ``BACKTEST_CERTIFIED`` it is v1's shadow gate with
    the days row at ``>= shadow.min_days``, followed by a round-trip row and a
    cap row. Every other state gets v1's gate unchanged.

    Args:
        record: The v2 strategy's record, after ``apply_guardrails``.
        evidence: The submitted evidence; ``shadow.days`` is the shadow's
            length when it ended (#1021 D-2).
        now: Current UTC time.
        v1: v1's pre-registration, whose thresholds v2 keeps.
        rules: v2's DSR floor and funnel thresholds.
        shadow: v2's shadow length rule.
        funnel: The run's validated funnel evidence; ``None`` fails the
            backtest gate.

    Returns:
        The :class:`GateReport` for the next transition.
    """
    report = evaluate_next_gate(record, evidence, now=now, pre_reg=v1)
    if record.state == CertificationState.BACKTEST_CERTIFIED:
        rows = tuple(
            new
            for row in report.checks
            for new in (
                _length_rows(row, evidence, shadow) if row.name == "shadow_days" else (row,)
            )
        )
        return replace(report, checks=rows)
    if record.state != CertificationState.UNTRUSTED:
        return report
    dsr = evidence.backtest.dsr if evidence.backtest else None
    rows = tuple(
        _v2_dsr(row, dsr, rules.dsr_min) if row.name == "dsr" else row for row in report.checks
    )
    return replace(report, checks=(*rows, *_funnel_rows(funnel, rules.funnel)))
