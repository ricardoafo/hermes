"""v2's backtest gate: pre-registration-v2.md §7, §9 and §10 for ``swing-mvp1-v2``.

v2 keeps v1's lifecycle and §9 secondary rows and changes the backtest gate to
DSR ≥ 0.95 plus the ADR-021 §5 funnel. :func:`parse_v2_backtest_rules` reads
the DSR floor from the signed v2 file's §10 text and the funnel thresholds
from its front matter; the secondary thresholds stay v1's, read from v1's
accessor, and the parser refuses a v2 file whose §9 no longer says they are
unchanged. :func:`evaluate_v2_gate` evaluates v1's backtest gate, replaces its
``dsr`` row and appends the funnel rows, so v1's gate code is untouched.

Only the gate out of ``UNTRUSTED`` exists for v2: v2 §7 changes the shadow
rule, so every later gate is refused until it is implemented.

Reference: pre-registration-v2.md §0, §7, §9, §10; ADR-021 §5;
openspec/changes/v2-certify-gate/change.md.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
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
)
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.preregistration import PreRegistration

__all__ = [
    "V2_STRATEGY_ID",
    "V2BacktestRules",
    "V2RulesError",
    "evaluate_v2_gate",
    "parse_v2_backtest_rules",
]

V2_STRATEGY_ID: Final = "swing-mvp1-v2"
"""The strategy and experiment id pre-registration v2 signs."""

_DSR_FLOOR = re.compile(r"^- DSR < (\d+(?:\.\d+)?);$")
_UNCHANGED = "Unchanged from v1"
_EVALUATED_HERE = frozenset(
    {CertificationState.UNTRUSTED, CertificationState.DEAD, CertificationState.CERTIFIED}
)


class V2RulesError(ValueError):
    """The v2 pre-registration does not state its backtest gate as this module reads it."""


@dataclass(frozen=True, slots=True)
class V2BacktestRules:
    """What v2's backtest gate adds to v1's: the DSR floor and the funnel thresholds."""

    dsr_min: float
    """The backtest passes the DSR row at or above this (v2 §10 kills below it)."""

    funnel: FunnelThresholds
    """The ``backtest_funnel`` front matter block."""


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

    secondary = [line for line in _subsection(document, "9.", "Secondary") if line.strip()]
    if not secondary or not secondary[0].startswith(_UNCHANGED):
        raise V2RulesError(f"§9 secondary metrics no longer say {_UNCHANGED!r}")

    floors = [
        float(match.group(1))
        for line in _subsection(document, "10.", "Backtest phase")
        if (match := _DSR_FLOOR.match(line.strip()))
    ]
    if len(floors) != 1:
        raise V2RulesError(f"§10 backtest phase states {len(floors)} DSR floors, not 1")
    return V2BacktestRules(dsr_min=floors[0], funnel=funnel)


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


def evaluate_v2_gate(
    record: CertificationRecord,
    evidence: GateEvidence,
    *,
    now: datetime,
    v1: PreRegistration,
    rules: V2BacktestRules,
    funnel: FunnelEvidence | None,
) -> GateReport:
    """Evaluate the gate out of a v2 record's current state.

    Pure, like :func:`evaluate_next_gate`. Out of ``UNTRUSTED`` it is v1's
    backtest gate with the ``dsr`` row at ``>= rules.dsr_min`` and the funnel
    rows appended; ``DEAD`` and ``CERTIFIED`` are refused as in v1; every
    other state is refused.

    Args:
        record: The v2 strategy's record, after ``apply_guardrails``.
        evidence: The submitted evidence.
        now: Current UTC time.
        v1: v1's pre-registration, whose §9 secondary thresholds v2 keeps.
        rules: v2's DSR floor and funnel thresholds.
        funnel: The run's validated funnel evidence; ``None`` fails the gate.

    Returns:
        The :class:`GateReport` for the next transition.
    """
    if record.state not in _EVALUATED_HERE:
        return GateReport(
            record.strategy_id,
            record.state,
            None,
            refused_reason=(
                f"the gate out of {record.state} is not implemented for {V2_STRATEGY_ID}; "
                "pre-registration v2 §7 changes the shadow rule, so v1's gate does not apply."
            ),
        )
    report = evaluate_next_gate(record, evidence, now=now, pre_reg=v1)
    if record.state != CertificationState.UNTRUSTED:
        return report
    dsr = evidence.backtest.dsr if evidence.backtest else None
    rows = tuple(
        _v2_dsr(row, dsr, rules.dsr_min) if row.name == "dsr" else row for row in report.checks
    )
    return replace(report, checks=(*rows, *_funnel_rows(funnel, rules.funnel)))
