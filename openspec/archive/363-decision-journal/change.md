# Change: 363-decision-journal

**SDD change**: `363-decision-journal`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#363)

## Summary

Adds the Code Owner's decision journal from the supervisor desk (ADR-012
D-05). `hermes journal add` records a decision with its options, choice,
reasoning, expected outcome and review date. `due` lists what is ready to
score. `review` scores it with evidence. The journal is an append-only,
hash-chained JSONL file, so an edit, a reordering or a deletion is detected.
Decisions whose outcome depends on strategy results stay sealed until the
strategy is OOS-certified, so no review can reveal shadow P&L (ruling 1).

## Scope

### In

- `domain/journal/entries.py`: `Decision`, `Review`, `OutcomeKind`, `Score`.
- `ports/decision_journal.py`: append-only port, with no update or delete.
- `adapters/journal/jsonl_store.py`: hash-chained JSONL store.
- `application/journal/service.py`: record, review and due rules.
- `entrypoints/journal.py` and the `hermes journal` subcommand.

### Out

- The scheduled routine that runs `hermes journal due` and posts the list:
  supervisor-desk wiring (#34).
- Scoring by an AI reviewer: a follow-up. This change records human or
  routine-written reviews.

## Decisions

### D-1: Two outcome kinds, and strategy decisions are sealed

- **Decision**: each decision is `process` or `strategy`. A `strategy`
  decision cannot be reviewed until the certification record is
  OOS_CERTIFIED, LIVE_APPROVED or CERTIFIED. Until then `due` lists it as
  sealed, without scoring it.
- **Rationale**: scoring a strategy call means looking at its results, which
  would reveal shadow P&L (ruling 1, ADR-012 D-06).
- **Cost of reversal**: LOW.

### D-2: Hash-chained JSONL file next to the ledger

- **Decision**: one JSON line per entry, each carrying the previous line's
  hash. The default path is `decision-journal.jsonl` beside the ledger, and
  `--journal` overrides it.
- **Alternatives**: a table in the SQLite ledger (a schema change for a
  low-volume log); files in the repo (needs a commit per decision).
- **Cost of reversal**: LOW.

### D-3: One review per decision; corrections are new decisions

- **Decision**: a decision is reviewed once, on or after its review date. A
  correction is a new decision with `--corrects ID`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Append-only, sealed, tested

- **A-1.1**: `uv run pytest tests/unit/application/journal tests/unit/entrypoints/test_journal.py`

## Design

See the module docstrings.

## Test plan

1. Round trip, duplicate ids, unknown corrections and field validation.
2. Editing or deleting a stored line raises `JournalIntegrityError`.
3. A review before its date is refused; a second review is refused.
4. A strategy decision is sealed without certification and unsealed with it.
5. The CLI paths for `add`, `list`, `due` and `review`, with their exit codes.
6. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- **A decision is mislabelled `process` when its outcome depends on
  strategy results**: its review could then touch P&L. The `--kind` flag is
  required knowledge for the author, and the review text is the author's
  own.
- **The file is lost**: it lives beside the ledger and is covered by the
  same backup.
