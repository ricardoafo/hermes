"""``hermes batch check``: check a research batch document (ADR-026 §1).

Prints one ``PASS``/``FAIL`` line per check, in the order of
:data:`~hermes.application.calibration.research_batch.BATCH_CHECK_NAMES`,
then ``BATCH PASSED`` or ``BATCH FAILED``. The backtest workflow runs it with
``--require-signed`` before a batch starts; the batch's own PR runs it in CI.
Nothing is recorded.

Exit codes: 0 every check passed, 1 a check failed, 2 usage, 3 the file, the
roster or the repository cannot be read.

Reference: ADR-026 §1, §2; issue #1057; research-funnel interface contract §1.
"""

import argparse
import json
from pathlib import Path
from typing import NoReturn, TextIO

import yaml

from hermes.adapters.git_history.subprocess_git import SubprocessGit
from hermes.application.calibration.research_batch import BatchError, check_batch, parse_batch
from hermes.application.strategy_protocol.strategy_factory import strategy_kind
from hermes.domain.errors import InfraError
from hermes.entrypoints._streams import resolve_err

__all__ = ["main"]

_ROSTER = Path("config/strategies.yaml")

_USAGE = (
    "Usage: hermes batch check FILE [--repo DIR] [--require-signed] [--candidates-out FILE]\n"
    "\n"
    "Checks a research batch (ADR-026 §1): 2 to 6 candidates, one per idea family,\n"
    "a summary and a pre-registration section for each, at most 3 free parameters,\n"
    "a backtest_funnel no looser than the ADR-021 §5 floors, the selection rule, the\n"
    "signing hash, each hypothesis card's hash, each strategy id and its parameters in\n"
    "each candidate's code against the commit the batch binds. --repo is the clone\n"
    "that holds FILE (default: the current directory); cards and code are read at its\n"
    "HEAD. --require-signed also fails a DRAFT batch. --candidates-out writes the\n"
    "candidates for the workflow matrix. Prints one PASS/FAIL line per check.\n"
    "Exit codes: 0 passed, 1 a check failed, 2 usage, 3 file or repository error.\n"
)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Parse ``argv`` and run ``hermes batch``.

    Args:
        argv: Arguments after the ``batch`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = _Parser(prog="hermes batch", add_help=False)
    parser.add_argument("action", nargs="?")
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--candidates-out", type=Path, default=None)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        err.write(f"ERROR: {exc}\n" + _USAGE)
        return 2
    if args.help:
        stream.write(_USAGE)
        return 0
    if args.action != "check" or args.file is None:
        err.write("ERROR: expected `check FILE`.\n" + _USAGE)
        return 2
    return _check(args, stream, err)


def _roster(repo: Path) -> dict[str, dict[str, object]]:
    """Every strategy name in ``config/strategies.yaml``, with its kind's default parameters."""
    parsed = yaml.safe_load((repo / _ROSTER).read_text(encoding="utf-8"))
    entries = parsed.get("strategies") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{_ROSTER} has no strategies list")
    roster: dict[str, dict[str, object]] = {}
    for entry in entries:
        if isinstance(entry, dict) and "name" in entry:
            signal = entry.get("signal")
            kind = strategy_kind(None if signal is None else str(signal))
            roster[str(entry["name"])] = kind.default_parameters()
    return roster


def _check(args: argparse.Namespace, stream: TextIO, err: TextIO) -> int:
    file: Path = args.file
    repo: Path = args.repo
    try:
        text = file.read_text(encoding="utf-8")
        path = file.resolve().relative_to(repo.resolve()).as_posix()
    except (OSError, UnicodeDecodeError) as exc:
        err.write(f"ERROR: batch file: {exc}\n")
        return 3
    except ValueError:
        err.write(f"ERROR: {file} is not inside the repository {repo}\n")
        return 3
    try:
        known = _roster(repo)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        err.write(f"ERROR: roster: {exc}\n")
        return 3
    try:
        checks = check_batch(
            path,
            text,
            SubprocessGit(repo),
            known_strategies=known,
            strategy_parameters=known,
            require_signed=args.require_signed,
        )
    except InfraError as exc:
        err.write(f"ERROR: repository: {exc}\n")
        return 3
    stream.write(f"# hermes batch check {path}\n")
    for check in checks:
        mark = "PASS" if check.passed else "FAIL"
        stream.write(f"{mark}  {check.name:<18} {check.detail}\n")
    passed = all(check.passed for check in checks)
    if args.candidates_out is not None:
        code = _write_candidates(args.candidates_out, path, text, err)
        if code:
            return code
    stream.write("BATCH PASSED\n" if passed else "BATCH FAILED\n")
    return 0 if passed else 1


def _write_candidates(out: Path, path: str, text: str, err: TextIO) -> int:
    """Write the workflow matrix; a batch that does not parse writes nothing."""
    try:
        batch = parse_batch(path, text)
    except BatchError:
        return 0
    body = {
        "batch_id": batch.batch_id,
        "candidates": [
            {"strategy_id": c.strategy_id, "idea_family": c.idea_family} for c in batch.candidates
        ],
    }
    try:
        out.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        err.write(f"ERROR: candidates-out: {exc}\n")
        return 3
    return 0
