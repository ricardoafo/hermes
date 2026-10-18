"""``hermes funnel --batch``: judge every candidate of a signed research batch at one stage.

Reads each candidate's evidence folder, judges it with the generic gate and one
trial count for the whole log (ADR-026 §4), and writes the stage's outputs
under ``--out``:

- ``verdicts/<strategy_id>.json``: the verdict record, statuses only (contract §5);
- ``validation-candidates.json`` after Train, ``selection.json`` after Validation;
- ``funnel-batch.txt``: every check with its observed value and requirement.

Stdout carries only ``PASS``/``FAIL``/``NA <strategy_id> <check>`` lines and the
``CANDIDATES`` or ``CHAMPION`` line: no metric value (ADR-023 §7, ADR-012 D-06).

Exit codes: 0 a candidate goes on (Train) or a champion exists (Validation),
1 none, or the batch is refused, 2 usage, 3 batch, trial-log, evidence or
output error.

Reference: ADR-026 §3-§5; issue #1059; the research funnel interface contract §4.
"""

import argparse
import json
from pathlib import Path
from typing import Final, NoReturn, TextIO

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.calibration.batch_stage import (
    StageOutcome,
    judge_stage,
    stage_summary,
    verdict_record,
)
from hermes.application.calibration.research_batch import BatchError, ResearchBatch, load_batch
from hermes.domain.errors import TrialLogError
from hermes.entrypoints._streams import resolve_err

__all__ = ["main"]

USAGE: Final = (
    "Usage: hermes funnel --batch FILE --stage train|validation --evidence-dir DIR\n"
    "                     --trial-log FILE --out DIR\n"
    "\n"
    "Judges every candidate of a SIGNED research batch with the generic gate and one\n"
    "trial count for the whole log. --evidence-dir holds DIR/<strategy_id>/ with\n"
    "performance.json, evidence.json and funnel-evidence.json; a candidate without one\n"
    "fails missing_evidence. Writes verdicts/, validation-candidates.json (train) or\n"
    "selection.json (validation) and funnel-batch.txt under --out.\n"
    "Exit codes: 0 a candidate goes on / a champion, 1 none or refused, 2 usage,\n"
    "3 batch, trial log, evidence or output error.\n"
)

_REQUIRED: Final = ("batch", "stage", "evidence_dir", "trial_log", "out")


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def main(argv: list[str], *, stream: TextIO, err: TextIO | None = None) -> int:
    """Parse ``argv`` and run ``hermes funnel --batch``.

    Args:
        argv: Arguments after the ``funnel`` token, ``--batch`` among them.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = _Parser(prog="hermes funnel", add_help=False)
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--stage", choices=("train", "validation"))
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--trial-log", type=Path)
    parser.add_argument("--out", type=Path)
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        err.write(f"ERROR: {exc}\n" + USAGE)
        return 2
    missing = [f"--{name.replace('_', '-')}" for name in _REQUIRED if getattr(args, name) is None]
    if missing:
        err.write(f"ERROR: --batch needs {', '.join(missing)}.\n" + USAGE)
        return 2
    return _run(args, stream, err)


def _run(args: argparse.Namespace, stream: TextIO, err: TextIO) -> int:
    batch = _signed_batch(args.batch, stream, err)
    if isinstance(batch, int):
        return batch
    stream.write(f"# hermes funnel --batch {batch.batch_id} --stage {args.stage}\n")
    if not args.trial_log.is_file():
        err.write(f"ERROR: trial log {args.trial_log} does not exist\n")
        return 3
    try:
        records = JsonlTrialLog(args.trial_log).records()
        outcome = judge_stage(batch, args.stage, args.evidence_dir, records)
    except TrialLogError as exc:
        err.write(f"ERROR: trial log: {exc}\n")
        return 3
    except (OSError, ValueError) as exc:
        err.write(f"ERROR: batch {batch.batch_id} {args.stage}: {exc}\n")
        return 3
    try:
        _write_outputs(args.out, outcome)
    except OSError as exc:
        err.write(f"ERROR: output: {exc}\n")
        return 3
    return _report(stream, outcome)


def _signed_batch(path: Path, stream: TextIO, err: TextIO) -> ResearchBatch | int:
    try:
        batch = load_batch(path)
    except (BatchError, OSError) as exc:
        err.write(f"ERROR: batch: {exc}\n")
        return 3
    if batch.status != "SIGNED":
        stream.write(
            f"# hermes funnel --batch {batch.batch_id}\n"
            f"REFUSED: batch {batch.batch_id} is {batch.status}; only a SIGNED batch is judged\n"
        )
        return 1
    return batch


def _write_outputs(out: Path, outcome: StageOutcome) -> None:
    verdicts = out / "verdicts"
    verdicts.mkdir(parents=True, exist_ok=True)
    for verdict in outcome.verdicts:
        _write_json(verdicts / f"{verdict.strategy_id}.json", verdict_record(outcome, verdict))
    summary = "validation-candidates.json" if outcome.stage == "train" else "selection.json"
    _write_json(out / summary, stage_summary(outcome))
    (out / "funnel-batch.txt").write_text(_details(outcome), encoding="utf-8")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _details(outcome: StageOutcome) -> str:
    lines = [f"# batch {outcome.batch_id} stage {outcome.stage}"]
    for verdict in outcome.verdicts:
        lines.append(f"## {verdict.strategy_id}")
        lines.extend(f"{c.status:<4} {c.check:<24} {c.detail}" for c in verdict.checks)
    lines.append(_closing_line(outcome))
    return "\n".join(lines) + "\n"


def _closing_line(outcome: StageOutcome) -> str:
    if outcome.stage == "train":
        return f"CANDIDATES {' '.join(outcome.goes_on) or 'none'}"
    return f"CHAMPION {outcome.champion or 'none'}"


def _report(stream: TextIO, outcome: StageOutcome) -> int:
    stream.writelines(
        f"{check.status} {verdict.strategy_id} {check.check}\n"
        for verdict in outcome.verdicts
        for check in verdict.checks
    )
    stream.write(_closing_line(outcome) + "\n")
    went_on = bool(outcome.goes_on) if outcome.stage == "train" else outcome.champion is not None
    return 0 if went_on else 1
