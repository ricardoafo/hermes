# Runbook — when things go wrong

Recovery procedures. Symptom → Diagnosis → Recovery → Post-incident
review → Prevention link. Per
[ADR-009](../architecture/adr-009-documentation-strategy.md) §5
(Runbook).

## When a runbook entry is warranted

- An incident actually happened OR is likely to happen.
- The recovery procedure is non-obvious under stress.
- A future-Ricardo (or future-collaborator) waking up at 3 AM needs to
  get to a fix in <5 minutes without reading prose.

## Entry format

Filename: `<symptom-or-system>.md` — descriptive, searchable at a glance.

Body:

```markdown
# {Symptom or System}

**Last exercised**: YYYY-MM-DD (drill or real incident)
**Owner**: Ricardo
**Related**: ADR-NNN, related runbook entries

## Symptom

What the operator sees. Log lines, Telegram alert body, chart shape.
Copy-paste from real observations, not paraphrased.

## Diagnosis

Ordered checks. Fastest signal first. Each check should have an
expected output and what "wrong" looks like.

## Recovery

Ordered steps. Copy-pasteable commands. No prose reasoning between
steps — reasoning belongs in the ADR or the decisions journal.

## Post-incident review

Template for the retrospective issue that gets opened after every
runbook execution:
- What broke?
- Why did it break?
- Why didn't monitoring catch it?
- Is there a code fix that prevents recurrence?

## Prevention

Link to the ADR, decisions-journal entry, or follow-up issue that
encodes the fix so this doesn't happen again.
```

## Living index

- [`vps-restart-procedure.md`](vps-restart-procedure.md) — restart a
  degraded service or the whole VPS
- [`cve-response.md`](cve-response.md) — triage and patch a CVE in a
  dependency, Action, runtime or the VPS OS (ADR-010)

## Planned entries (Phase 0+)

- `exchange-failover.md` — Binance → Kraken cold backup drill (ADR-004)
- `tax-lot-corruption-recovery.md` — FIFO ledger repair (ADR-002 + §03)
- `llm-supervisor-mcp-outage.md` — Phase 3+ (ADR-003 §08)
