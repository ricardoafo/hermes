"""``hermes journal`` — the Code Owner's decision journal.

Subcommands:

- ``add``: record a decision with its options, reasoning, expected outcome
  and review date. ``--corrects ID`` records a correction as a new entry.
- ``due``: list decisions whose review date has passed. Strategy decisions
  show as sealed until certification. A scheduled routine runs this.
- ``review ID``: score a due decision with its actual outcome and evidence.
- ``list``: print every entry.

Whether the strategy is certified comes from the certification record
(#382): OOS_CERTIFIED, LIVE_APPROVED or CERTIFIED unseal strategy decisions.

Exit codes: 0 ok, 1 refused by a journal rule, 2 usage, 3 store error.

Reference: issue #363; openspec/changes/363-decision-journal.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import TextIO

import ulid

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.journal.service import (
    JournalError,
    due,
    record_decision,
    record_review,
)
from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.time import now_utc
from hermes.ports.certification import CertificationStoreError
from hermes.ports.decision_journal import JournalIntegrityError

__all__ = ["main"]

_UNSEALED = frozenset(
    {
        CertificationState.OOS_CERTIFIED,
        CertificationState.LIVE_APPROVED,
        CertificationState.CERTIFIED,
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes journal", exit_on_error=False)
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--store-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add")
    add.add_argument("--decision", required=True)
    add.add_argument("--option", action="append", required=True, dest="options")
    add.add_argument("--chosen", required=True)
    add.add_argument("--reasoning", required=True)
    add.add_argument("--expected", required=True)
    add.add_argument("--review-on", required=True, type=date.fromisoformat)
    add.add_argument("--kind", choices=[k.value for k in OutcomeKind], default="process")
    add.add_argument("--corrects", default=None)

    sub.add_parser("due")
    sub.add_parser("list")

    review = sub.add_parser("review")
    review.add_argument("decision_id")
    review.add_argument("--outcome", required=True)
    review.add_argument("--score", choices=[s.value for s in Score], required=True)
    review.add_argument("--evidence", action="append", required=True)
    return parser


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Run ``hermes journal``.

    Args:
        argv: Arguments after the ``journal`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit, ValueError) as exc:
        err.write(f"ERROR: could not parse `hermes journal` arguments: {exc}\n")
        return 2
    db_path = serve_module.DEFAULT_DB_PATH
    journal = JsonlDecisionJournal(args.journal or db_path.parent / "decision-journal.jsonl")
    store = JsonCertificationStore(args.store_dir or serve_module.certification_dir_for(db_path))
    try:
        record = store.load(serve_module.SHADOW_STRATEGY_NAME)
        certified = record is not None and record.state in _UNSEALED
        return _dispatch(args, journal, certified, stream)
    except JournalError as exc:
        err.write(f"REFUSED: {exc}\n")
        return 1
    except (JournalIntegrityError, CertificationStoreError, OSError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 3


def _dispatch(
    args: argparse.Namespace, journal: JsonlDecisionJournal, certified: bool, stream: TextIO
) -> int:
    now = now_utc()
    if args.command == "add":
        try:
            decision = Decision(
                entry_id=str(ulid.new()),
                recorded_at=now,
                decision=args.decision,
                options=tuple(args.options),
                chosen=args.chosen,
                reasoning=args.reasoning,
                expected_outcome=args.expected,
                review_on=args.review_on,
                outcome_kind=OutcomeKind(args.kind),
                corrects=args.corrects,
            )
        except ValueError as exc:
            raise JournalError(str(exc)) from exc
        record_decision(journal, decision)
        stream.write(f"RECORDED {decision.entry_id} (review on {decision.review_on})\n")
    elif args.command == "review":
        review = record_review(
            journal,
            entry_id=str(ulid.new()),
            decision_id=args.decision_id,
            actual_outcome=args.outcome,
            score=Score(args.score),
            evidence=tuple(args.evidence),
            now=now,
            certified=certified,
        )
        stream.write(f"REVIEWED {review.decision_id}: {review.score}\n")
    elif args.command == "due":
        report = due(journal, today=now.date(), certified=certified)
        for d in report.reviewable:
            stream.write(f"DUE     {d.entry_id}  {d.review_on}  {d.decision}\n")
        for d in report.sealed:
            stream.write(
                f"SEALED  {d.entry_id}  {d.review_on}  {d.decision} (until certification)\n"
            )
        if not report.reviewable and not report.sealed:
            stream.write("Nothing due.\n")
    else:
        for entry in journal.entries():
            stream.write(_line(entry) + "\n")
    return 0


def _line(entry: Decision | Review) -> str:
    if isinstance(entry, Decision):
        fix = f" (corrects {entry.corrects})" if entry.corrects else ""
        return (
            f"DECISION {entry.entry_id} {entry.recorded_at.date()} [{entry.outcome_kind}] "
            f"{entry.decision} -> {entry.chosen}; review {entry.review_on}{fix}"
        )
    return (
        f"REVIEW   {entry.entry_id} {entry.recorded_at.date()} of {entry.decision_id}: "
        f"{entry.score}"
    )
