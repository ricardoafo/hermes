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
**Author**: Ricardo Afonso (or claude on behalf of Ricardo)
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

None yet — populate as decisions accumulate.

## Anti-patterns

- **Not writing entries**. If it wasn't worth documenting, it wasn't
  worth deciding.
- **Editing entries after the fact**. Journal entries are timestamped
  snapshots. If the decision changes, write a new entry that
  references and supersedes the old one.
- **Long entries**. If it grows past 500 words, it's probably an ADR.
