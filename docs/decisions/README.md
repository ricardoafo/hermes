# Decisions journal — small, dated, easy to append

Small operational decisions that don't warrant a full ADR but that
future-Ricardo will want to find later. Per
[ADR-009](../architecture/adr-009-documentation-strategy.md) §4
(Decisions journal).

## When a decision goes here vs in an ADR

**Journal entry** (this folder):

- Operational choice (which library variant, which config value)
- One-way trade-off with low blast radius
- Reversible without a migration
- Fits in <500 words

**ADR** (`docs/architecture/`):

- Architectural choice affecting multiple modules
- One-way trade-off with high blast radius
- Reversal requires a migration or breaking change
- Cross-references multiple existing ADRs

If unsure: journal first, promote to ADR later if the choice keeps
resurfacing in reviews.

## Entry format

Filename: `YYYY-MM-DD-short-slug.md`

Body:

```markdown
# {title}

**Date**: YYYY-MM-DD
**Author**: Code Owner (or AI Bot on behalf of the Code Owner)
**Related**: ADR-NNN, prior journal entries, GitHub issues

## Decision

One sentence. What did we pick?

## Context

Why this came up. What alternatives were considered. What tradeoffs
mattered.

## Consequences

What this locks in. What it forecloses. What we'll revisit.
```

## Living index

| Date       | Entry                                                                                        | Supersedes                                    |
| ---------- | -------------------------------------------------------------------------------------------- | --------------------------------------------- |
| 2026-08-21 | [PR flow hardening](2026-08-21-pr-flow-hardening.md)                                         | —                                             |
| 2026-08-21 | [Revalidate-then-merge](2026-08-21-revalidate-then-merge.md)                                 | —                                             |
| 2026-08-21 | [Dependabot over Renovate](2026-08-21-dependabot-over-renovate.md)                           | ADR-010 §Renovate                             |
| 2026-08-21 | [Python 3.14 pin](2026-08-21-python-3-14-pin.md)                                             | ADR-003 §User Interface + README §64          |
| 2026-08-22 | [Drop `safety` from dev deps](2026-08-22-drop-safety.md)                                     | ADR-010 §3 CVE scanning (safety+pip-audit)    |
| 2026-08-22 | [ci-success aggregator fix](2026-08-22-ci-success-aggregator-fix.md)                         | Inline shell aggregator in ci.yml             |
| 2026-08-23 | [Add Slack alongside Telegram](2026-08-23-slack-alongside-telegram.md)                       | ADR-008 §4 Telegram alerts scope              |
| 2026-08-23 | [Binance testnet klines + SQLite kline store](2026-08-23-binance-testnet-klines.md)          | —                                             |

## Anti-patterns

- **Not writing entries**. If it wasn't worth documenting, it wasn't
  worth deciding.
- **Editing entries after the fact**. Journal entries are timestamped
  snapshots. If the decision changes, write a new entry that
  references and supersedes the old one.
- **Long entries**. If it grows past 500 words, it's probably an ADR.
