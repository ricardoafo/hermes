#!/usr/bin/env python3
"""Put a batch run's results into the working tree for the results PR (ADR-026 §6).

Takes what the backtest jobs produced and writes only what ADR-012 D-06 allows
an AI session to see: the new trial lines byte for byte, one verdict record per
candidate and stage with PASS/FAIL/NA per check and no metric value, and one
lab-notebook entry per verdict. Nothing here reads a performance file.

Usage:
    python scripts/backtest/assemble_results.py --repo-root . --experiment swing-mvp1 \
        --batch-id b1 --run-id 123 --commit SHA --chain trial-log.jsonl \
        --verdicts DIR [--verdicts DIR ...] [--selection selection.json] --notes-out FILE

Exit codes:
    0 - the working tree holds the results
    3 - an input is missing, malformed, or would write a value
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes.application.strategy_protocol.lab_notebook import LabNotebook, LabNotebookEntry

SCHEMA = "hermes.verdict/1"
STATUSES = frozenset({"PASS", "FAIL", "NA"})
VERDICT_KEYS = frozenset(
    {
        "schema",
        "batch_id",
        "strategy_id",
        "idea_family",
        "stage",
        "run_id",
        "commit",
        "window",
        "trial_lines",
        "checks",
        "passed",
        "goes_on",
        "champion",
    }
)
# The only numbers a verdict record may hold; every other field is a string, a boolean or a list.
NUMBER_KEYS = frozenset({"run_id", "trial_lines"})
STAGES = frozenset({"train", "validation"})
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Gate check names are letters and underscores; no digit or dot, so a name cannot carry a value.
_CHECK_NAME = re.compile(r"^[a-z][a-z_]*$")


class ResultsError(Exception):
    """An input is missing or malformed, or a record would hold a value."""


def validate_verdict(verdict: dict[str, Any]) -> None:
    """Raise ``ResultsError`` unless ``verdict`` is a value-free ``hermes.verdict/1`` record."""
    unknown = set(verdict) - VERDICT_KEYS
    if unknown:
        raise ResultsError(f"verdict has unknown fields {sorted(unknown)}")
    if verdict.get("schema") != SCHEMA:
        raise ResultsError(f"verdict schema is not {SCHEMA}")
    for field in ("batch_id", "strategy_id", "idea_family"):
        if not _SAFE_NAME.match(str(verdict.get(field, ""))):
            raise ResultsError(f"verdict {field} is not a plain name")
    if verdict.get("stage") not in STAGES:
        raise ResultsError("verdict stage is not train or validation")
    for key, value in verdict.items():
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and key not in NUMBER_KEYS
        ):
            raise ResultsError(f"verdict field {key} holds a number")
    for key in NUMBER_KEYS:
        value = verdict.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ResultsError(f"verdict {key} is not a whole number or null")
    trial_lines = verdict.get("trial_lines")
    if trial_lines is not None and trial_lines < 0:
        raise ResultsError("verdict trial_lines is negative")
    if not isinstance(verdict.get("passed"), bool):
        raise ResultsError("verdict passed is not a boolean")
    for key in ("goes_on", "champion"):
        if verdict.get(key) is not None and not isinstance(verdict[key], bool):
            raise ResultsError(f"verdict {key} is not a boolean or null")
    window = verdict.get("window") or {}
    if set(window) != {"start", "end"} or not all(_DATE.match(str(v)) for v in window.values()):
        raise ResultsError("verdict window is not a start and end date")
    checks = verdict.get("checks")
    if not isinstance(checks, list):
        raise ResultsError("verdict checks is not a list")
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != {"check", "status"}
            or check["status"] not in STATUSES
            or not _CHECK_NAME.match(str(check["check"]))
        ):
            raise ResultsError("a verdict check is not {check, status} with PASS, FAIL or NA")


def append_trial_lines(committed: Path, chain: Path | None) -> int:
    """Append the chain's new lines to ``committed`` byte for byte; return how many lines."""
    if chain is None or not chain.exists():
        return 0
    base = committed.read_bytes()
    extended = chain.read_bytes()
    if not extended.startswith(base):
        raise ResultsError("the trial log chain does not start with the committed log")
    new = extended[len(base) :]
    if new:
        with committed.open("ab") as handle:
            handle.write(new)
    return new.count(b"\n")


def load_verdicts(sources: list[Path], batch_id: str) -> list[dict[str, Any]]:
    """Read and validate every verdict before anything is written."""
    verdicts: list[dict[str, Any]] = []
    for source in sorted(path for directory in sources for path in directory.glob("*.json")):
        verdict = json.loads(source.read_text(encoding="utf-8"))
        validate_verdict(verdict)
        if verdict["batch_id"] != batch_id:
            raise ResultsError(f"{source.name} belongs to batch {verdict['batch_id']}")
        verdicts.append(verdict)
    return verdicts


def write_verdicts(
    verdicts: list[dict[str, Any]], target: Path, *, run_id: int, commit: str
) -> list[dict[str, Any]]:
    """Fill the run and commit into each validated verdict and write it under ``target``."""
    written: list[dict[str, Any]] = []
    for verdict in verdicts:
        record = {**verdict, "run_id": run_id, "commit": commit}
        target.mkdir(parents=True, exist_ok=True)
        name = f"{record['strategy_id']}.{record['stage']}.json"
        (target / name).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        written.append(record)
    return written


def notebook_entry(verdict: dict[str, Any], *, experiment: str, now: datetime) -> LabNotebookEntry:
    """Return the PASS/FAIL-only notebook entry for one verdict."""
    stage = verdict["stage"]
    outcome = "goes on to Validation" if verdict.get("goes_on") else "does not go on"
    decision = (
        (f"Champion of batch {verdict['batch_id']}.",)
        if verdict.get("champion")
        else ((f"{verdict['strategy_id']} {outcome}.",) if stage == "train" else ())
    )
    return LabNotebookEntry(
        timestamp_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase=stage,
        actor="engine",
        action=f"{verdict['strategy_id']} {stage} verdict in batch {verdict['batch_id']}",
        observed_metrics=tuple(f"{c['check']} = {c['status']}" for c in verdict["checks"]),
        decision=decision,
        rationale=(
            "ADR-026 §6: the results job records the batch verdict; no agent handles a value.",
            "PASS/FAIL only (ADR-012 D-06, ADR-023 §7); the values stay in the run's artifact.",
            "Not a deviation per §11: no signed rule, window, metric or threshold changes.",
        ),
        files_touched=(
            f"docs/experiments/{experiment}/verdicts/{verdict['batch_id']}/"
            f"{verdict['strategy_id']}.{stage}.json (added)",
            f"docs/experiments/{experiment}/trial-log.jsonl (modified)",
            f"docs/experiments/{experiment}/lab-notebook.md (modified)",
        ),
    )


def champion_of(selection: Path | None) -> str | None:
    """Return the champion's strategy id from ``selection.json``, or ``None``."""
    if selection is None or not selection.exists():
        return None
    champion = json.loads(selection.read_text(encoding="utf-8")).get("champion")
    if champion is None:
        return None
    if not _SAFE_NAME.match(str(champion)):
        raise ResultsError("the champion is not a plain strategy id")
    return str(champion)


def assemble(args: argparse.Namespace, now: datetime) -> str:
    """Write the results into the tree and return the PR's summary lines."""
    for field in ("experiment", "batch_id"):
        if not _SAFE_NAME.match(getattr(args, field)):
            raise ResultsError(f"{field} is not a plain name")
    experiment_dir = args.repo_root / "docs" / "experiments" / args.experiment
    loaded = load_verdicts(args.verdicts, args.batch_id)
    if args.require_chain and loaded and (args.chain is None or not args.chain.exists()):
        raise ResultsError(
            "verdicts exist but the trial log chain is missing, so no lines would count"
        )
    lines = append_trial_lines(experiment_dir / "trial-log.jsonl", args.chain)
    verdicts = write_verdicts(
        loaded,
        experiment_dir / "verdicts" / args.batch_id,
        run_id=args.run_id,
        commit=args.commit,
    )
    notebook = LabNotebook(experiment_dir / "lab-notebook.md")
    for verdict in verdicts:
        notebook.append(notebook_entry(verdict, experiment=args.experiment, now=now))
    champion = champion_of(args.selection)
    summary = [f"- New trial lines: {lines}", f"- Verdict records: {len(verdicts)}"]
    for verdict in verdicts:
        status = "PASS" if verdict["passed"] else "FAIL"
        summary.append(f"- {verdict['strategy_id']} {verdict['stage']}: {status}")
    if args.selection is not None:
        summary.append(f"- Champion: {champion or 'none'}")
    return "\n".join(summary) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and assemble the results."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--chain", type=Path, default=None)
    parser.add_argument(
        "--require-chain",
        action="store_true",
        help="fail when verdicts exist but the trial log chain does not",
    )
    parser.add_argument("--verdicts", type=Path, action="append", default=[])
    parser.add_argument("--selection", type=Path, default=None)
    parser.add_argument("--notes-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        notes = assemble(args, datetime.now(UTC))
    except (ResultsError, OSError, ValueError, KeyError, TypeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    args.notes_out.write_text(notes, encoding="utf-8")
    print(notes, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
