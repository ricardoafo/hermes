"""``hermes journal`` — the Code Owner's decision journal.

Subcommands:

- ``add``: record a decision with its options, reasoning, expected outcome
  and review date. ``--corrects ID`` records a correction as a new entry.
- ``due``: list decisions whose review date has passed. Strategy decisions
  show as sealed until certification. A scheduled routine runs this.
- ``review ID``: score a due decision with its actual outcome and evidence.
- ``list``: print every entry; ``--json`` prints one JSON document instead.
- ``repair``: drop a torn last line a power loss left (issue #891). Without
  ``--confirm REPAIR`` it only says what it would drop.

``due --json`` prints ``{"reviewable": [...], "sealed": [...]}``; ``list --json``
prints ``{"entries": [...]}``. ``-h``/``--help`` works on ``hermes journal``
and on every subcommand.

Whether the strategy is certified comes from the certification record
(#382): OOS_CERTIFIED, LIVE_APPROVED or CERTIFIED unseal strategy decisions.

Exit codes: 0 ok, 1 refused by a journal rule, 2 usage, 3 store error.

Reference: issue #363; openspec/archive/363-decision-journal.
"""

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from ulid import ULID

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
from hermes.application.journal.service import (
    JournalError,
    due,
    record_decision,
    record_review,
)
from hermes.domain.calibration.certification import CertificationState
from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.time import now_utc
from hermes.ports.certification import CertificationStoreError
from hermes.ports.decision_journal import JournalIntegrityError

__all__ = ["REPAIR_CONFIRM_WORD", "main"]

REPAIR_CONFIRM_WORD: Final = "REPAIR"

_UNSEALED = frozenset(
    {
        CertificationState.OOS_CERTIFIED,
        CertificationState.LIVE_APPROVED,
        CertificationState.CERTIFIED,
    }
)


_EXIT_CODES: Final = "Exit codes: 0 ok, 1 refused by a journal rule, 2 usage, 3 store error."
_HELP_FLAGS: Final = frozenset({"-h", "--help"})


def _new_parser(**kwargs: Any) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(add_help=False, epilog=_EXIT_CODES, **kwargs)


def _parser() -> argparse.ArgumentParser:
    return _build()[0]


def _build() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = _new_parser(prog="hermes journal", exit_on_error=False)
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--store-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", add_help=False, epilog=_EXIT_CODES)
    add.add_argument("--decision", required=True)
    add.add_argument("--option", action="append", required=True, dest="options")
    add.add_argument("--chosen", required=True)
    add.add_argument("--reasoning", required=True)
    add.add_argument("--expected", required=True)
    add.add_argument("--review-on", required=True, type=date.fromisoformat)
    add.add_argument("--kind", choices=[k.value for k in OutcomeKind], default="process")
    add.add_argument("--corrects", default=None)

    for name in ("due", "list"):
        sub_parser = sub.add_parser(name, add_help=False, epilog=_EXIT_CODES)
        sub_parser.add_argument("--json", action="store_true", dest="as_json")

    review = sub.add_parser("review", add_help=False, epilog=_EXIT_CODES)
    review.add_argument("decision_id")
    review.add_argument("--outcome", required=True)
    review.add_argument("--score", choices=[s.value for s in Score], required=True)
    review.add_argument("--evidence", action="append", required=True)

    repair = sub.add_parser("repair", add_help=False, epilog=_EXIT_CODES)
    repair.add_argument("--confirm", default="")
    return parser, dict(sub.choices)


def _help_text(argv: list[str]) -> str | None:
    """Return the help for the parser ``argv`` addresses, or ``None`` without a help flag."""
    if not _HELP_FLAGS.intersection(argv):
        return None
    parser, subparsers = _build()
    for token in argv:
        if token in subparsers:
            return subparsers[token].format_help()
    return parser.format_help()


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
    help_text = _help_text(argv)
    if help_text is not None:
        stream.write(help_text)
        return 0
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
    if args.command == "repair":
        return _repair(
            journal, confirmed=args.confirm == REPAIR_CONFIRM_WORD, now=now, stream=stream
        )
    if args.command == "add":
        try:
            decision = Decision(
                entry_id=str(ULID()),
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
            entry_id=str(ULID()),
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
        if args.as_json:
            payload = {
                "reviewable": [_as_json(d) for d in report.reviewable],
                "sealed": [_as_json(d) for d in report.sealed],
            }
            stream.write(json.dumps(payload, default=_json_default) + "\n")
            return 0
        stream.writelines(
            f"DUE     {d.entry_id}  {d.review_on}  {d.decision}\n" for d in report.reviewable
        )
        stream.writelines(
            f"SEALED  {d.entry_id}  {d.review_on}  {d.decision} (until certification)\n"
            for d in report.sealed
        )
        if not report.reviewable and not report.sealed:
            stream.write("Nothing due.\n")
    elif args.as_json:
        stream.write(
            json.dumps({"entries": [_as_json(e) for e in journal.entries()]}, default=_json_default)
            + "\n"
        )
    else:
        stream.writelines(_line(entry) + "\n" for entry in journal.entries())
    return 0


def _repair(
    journal: JsonlDecisionJournal, *, confirmed: bool, now: datetime, stream: TextIO
) -> int:
    torn = journal.torn_line()
    if torn is None:
        stream.write("Nothing to repair: the last line is whole.\n")
        return 0
    what = f"line {torn.number} ({torn.size} bytes, sha256 {torn.sha256})"
    if not confirmed:
        raise JournalError(
            f"the last line is torn: {what}. With --confirm {REPAIR_CONFIRM_WORD} it is dropped "
            "and the drop is recorded in the journal"
        )
    journal.repair(repaired_at=now)
    stream.write(f"REPAIRED: dropped {what}; the drop is recorded in the journal\n")
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


def _as_json(entry: Decision | Review) -> dict[str, Any]:
    kind = "decision" if isinstance(entry, Decision) else "review"
    return {"type": kind, **asdict(entry)}


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
