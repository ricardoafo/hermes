# Research — background investigations

Investigations that fed decisions. Not primary sources; digests of what
Ricardo (or Claude on his behalf) learned from external material. Per
[ADR-009](../architecture/adr-009-documentation-strategy.md) §6
(Research).

## When research goes here vs elsewhere

**Research entry** (this folder):

- Investigation that took >30 minutes and produced a decision
- Digest of external material (papers, articles, competitor analysis,
  regulatory bindings) with citations
- Reproducible — future-Ricardo could re-run the investigation

**Decision journal entry**:

- The *choice* that the research produced, in <500 words

**ADR**:

- The *architectural* consequence of the research, once locked

**Engram memory**:

- The *raw findings* while the research is in flight, or the compressed
  summary of a session

Research entries are the durable, external-references-linked version of
what starts in engram.

## Entry format

Filename: `YYYY-MM-topic-slug.md` — dated by *when the investigation
concluded*, not by publication date of the material.

Body:

```markdown
# {Topic}

**Date**: YYYY-MM-DD (investigation concluded)
**Author**: Ricardo / claude
**Fed decisions**: ADR-NNN, journal entry YYYY-MM-DD-...
**Sources**: bullet list with URLs, doc IDs, book chapters

## Question

What were we trying to answer?

## Findings

Structured summary. Cite sources inline. Distinguish confirmed facts
from working hypotheses.

## What this closes

Which decision is now decidable. Which alternatives are ruled out.

## What this opens

New questions surfaced. Follow-up research needed.
```

## Living index

| Entry | Fed decisions | Date |
| ----- | ------------- | ---- |
| [2026-08-feasibility.md](2026-08-feasibility.md) | ADR-001, ADR-005 | 2026-08 |

## Retention

Research entries are permanent. If the underlying source disappears
(paywalled article, deleted blog post), the digest here becomes the
canonical reference. Do not delete or rewrite entries; supersede with
new-dated entries.
