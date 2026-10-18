"""``hermes funnel`` — evaluate the ADR-021 §5 backtest funnel for a v2+ strategy.

Reads every threshold from the ``backtest_funnel:`` block of the signed
pre-registration's YAML front matter, reads the run's evidence file, and
prints one ``PASS``/``FAIL`` line per criterion in the ``hermes certify``
layout. Observed numbers stay on the check lines, as certify prints them; the
backtest workflow's summary keeps only the verdict and the name (ADR-023 §7).
Nothing is recorded.

Exit codes: 0 every check passed, 1 a check failed or the run was refused
(missing threshold, unsigned document, hash mismatch), 2 usage,
3 pre-registration file or evidence error.

Reference: ADR-021 §5; openspec/changes/v2-backtest-funnel/change.md.
"""

import argparse
import hashlib
from pathlib import Path
from typing import NoReturn, TextIO

import yaml

from hermes.application.calibration.backtest_funnel import (
    FunnelEvidenceError,
    FunnelThresholdError,
    FunnelThresholds,
    evaluate_funnel,
    parse_funnel_front_matter,
    validate_evidence,
)
from hermes.application.calibration.evidence_files import load_funnel_evidence
from hermes.entrypoints._streams import resolve_err

__all__ = ["load_funnel_evidence", "main", "read_front_matter"]

_USAGE = (
    "Usage: hermes funnel <strategy_id> --pre-registration FILE\n"
    "                     --evidence FILE --expect-sha256 HEX\n"
    "\n"
    "Evaluates the ADR-021 §5 backtest funnel. --expect-sha256 is the signed hash\n"
    "of the pre-registration; any other file is refused. Every threshold comes from the\n"
    "backtest_funnel block of the signed pre-registration's front matter; a missing\n"
    "one refuses the run. Prints one PASS/FAIL line per check. Records nothing.\n"
    "Exit codes: 0 passed, 1 failed or refused, 2 usage, 3 pre-registration/evidence.\n"
)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Parse ``argv`` and run ``hermes funnel``.

    Args:
        argv: Arguments after the ``funnel`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = _Parser(prog="hermes funnel", add_help=False)
    parser.add_argument("strategy_id", nargs="?")
    parser.add_argument("--pre-registration", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--expect-sha256", default=None)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        err.write(f"ERROR: {exc}\n" + _USAGE)
        return 2
    if args.help:
        stream.write(_USAGE)
        return 0
    if (
        not args.strategy_id
        or args.pre_registration is None
        or args.evidence is None
        or args.expect_sha256 is None
    ):
        err.write(
            "ERROR: strategy_id, --pre-registration, --evidence and --expect-sha256 "
            "are required.\n" + _USAGE
        )
        return 2
    return _run(args, stream, err)


def _run(args: argparse.Namespace, stream: TextIO, err: TextIO) -> int:
    thresholds = _judge_pre_registration(args, stream, err)
    if isinstance(thresholds, int):
        return thresholds
    try:
        evidence = validate_evidence(
            load_funnel_evidence(args.evidence, strategy_id=args.strategy_id), thresholds
        )
    except (OSError, FunnelEvidenceError) as exc:
        err.write(f"ERROR: evidence: {exc}\n")
        return 3
    checks = evaluate_funnel(evidence, thresholds)
    for check in checks:
        mark = "PASS" if check.passed else "FAIL"
        stream.write(
            f"{mark}  {check.name:<24} observed {check.observed:<22} required {check.requirement}\n"
        )
    passed = all(check.passed for check in checks)
    stream.write("FUNNEL PASSED\n" if passed else "FUNNEL FAILED\n")
    return 0 if passed else 1


def _judge_pre_registration(
    args: argparse.Namespace, stream: TextIO, err: TextIO
) -> FunnelThresholds | int:
    """Return the thresholds, or write the refusal or ERROR line and return the exit code.

    Runs before the evidence is opened, so a refusal exits 1 whatever the
    evidence holds. The hash is compared on the raw bytes before any parsing:
    a file that is not the signed one is refused, not diagnosed, and its
    unverified ``experiment_id`` is not printed.
    """
    try:
        raw = args.pre_registration.read_bytes()
    except OSError as exc:
        err.write(f"ERROR: pre-registration: {exc}\n")
        return 3
    digest = hashlib.sha256(raw).hexdigest()
    if args.expect_sha256.lower() != digest:
        _write_header(stream, args.strategy_id, "-", digest)
        stream.write(
            f"REFUSED: pre-registration sha256 {digest} does not match the expected "
            f"{args.expect_sha256}\n"
        )
        return 1
    try:
        front_matter = read_front_matter(raw.decode("utf-8"))
    except ValueError as exc:
        err.write(f"ERROR: pre-registration: {exc}\n")
        return 3
    _write_header(stream, args.strategy_id, front_matter.get("experiment_id") or "-", digest)
    try:
        return parse_funnel_front_matter(front_matter, strategy_id=args.strategy_id)
    except FunnelThresholdError as exc:
        stream.write(f"REFUSED: {exc}\n")
        return 1


def _write_header(stream: TextIO, strategy_id: str, experiment: object, digest: str) -> None:
    stream.write(
        f"# hermes funnel {strategy_id}: {experiment} (pre-registration sha256 {digest})\n"
    )


def read_front_matter(text: str) -> dict[str, object]:
    """Return the YAML front matter of a Markdown document.

    Args:
        text: The document.

    Returns:
        The mapping between the first two ``---`` lines.

    Raises:
        ValueError: No front matter, an unterminated block, invalid YAML, or
            YAML that is not a mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no YAML front matter (first line must be ---)")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError("YAML front matter is not closed by ---") from None
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("front matter must be a mapping")
    return {str(k): v for k, v in parsed.items()}
