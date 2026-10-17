"""``hermes trials`` — look after the trial log behind the Deflated Sharpe Ratio.

Subcommands:

- ``repair``: drop a torn last line a power loss or kill left in the trial log
  (issue #891). Without ``--confirm REPAIR`` it only says what it would drop.
  With it, the line is dropped and the drop is recorded as its own chain line,
  so the log keeps the trace that an append went missing. A line that parses
  but breaks the chain is an edit, not a torn write, and is refused.

The trial log is the trial count of pre-registration §9, so a repair never
runs on its own: every later backtest append refuses the torn log until the
Code Owner runs this command.

Exit codes: 0 repaired or nothing to repair, 1 refused (a torn line without
``--confirm REPAIR``), 2 usage, 3 the trial log cannot be read or written.

Reference: issue #891.
"""

import argparse
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.domain.errors import TrialLogError
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.time import now_utc

__all__ = ["DEFAULT_TRIAL_LOG", "REPAIR_CONFIRM_WORD", "main"]

DEFAULT_TRIAL_LOG: Final = Path("docs/experiments/swing-mvp1/trial-log.jsonl")
REPAIR_CONFIRM_WORD: Final = "REPAIR"

_EXIT_CODES: Final = (
    "Exit codes: 0 repaired or nothing to repair, 1 refused, 2 usage, 3 trial log error."
)
_HELP_FLAGS: Final = frozenset({"-h", "--help"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes trials", add_help=False, epilog=_EXIT_CODES, exit_on_error=False
    )
    sub = parser.add_subparsers(dest="command", required=True)
    repair = sub.add_parser("repair", add_help=False, epilog=_EXIT_CODES)
    repair.add_argument("--trial-log", type=Path, default=DEFAULT_TRIAL_LOG)
    repair.add_argument("--confirm", default="")
    return parser


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Run ``hermes trials``.

    Args:
        argv: Arguments after the ``trials`` token.
        stream: Output stream.
        err: Stream for ERROR and REFUSED lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    if _HELP_FLAGS.intersection(argv):
        stream.write(_parser().format_help())
        return 0
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit, ValueError) as exc:
        err.write(f"ERROR: could not parse `hermes trials` arguments: {exc}\n")
        return 2
    log = JsonlTrialLog(args.trial_log)
    try:
        torn = log.torn_line()
        if torn is None:
            stream.write(f"Nothing to repair: the last line of {log.path} is whole.\n")
            return 0
        what = f"line {torn.number} ({torn.size} bytes, sha256 {torn.sha256})"
        if args.confirm != REPAIR_CONFIRM_WORD:
            err.write(
                f"REFUSED: the last line of {log.path} is torn: {what}. With --confirm "
                f"{REPAIR_CONFIRM_WORD} it is dropped and the drop is recorded in the log\n"
            )
            return 1
        log.repair(repaired_at=now_utc())
    except TrialLogError as exc:
        err.write(f"ERROR: {exc}\n")
        return 3
    stream.write(f"REPAIRED: dropped {what} from {log.path}; the drop is recorded in the log\n")
    return 0
