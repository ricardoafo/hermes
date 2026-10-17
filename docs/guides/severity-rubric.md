# Severity Rubric

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (sole reviewer)
**Applies to**: everyone (and every AI agent) triaging a defect or writing a test for one.
**Description**: How Hermes ranks a defect or a PR: SEV-1 to SEV-4 levels, the security rows S1-S12 and the functional rows F1-F12 used by bug triage, the `sev-*` labels and the `# SEV-1:` test annotation.

`bug-triager`, the `sev-*` labels, the `# SEV-1: <row>` test annotation (enforced by
`tests/plugins/sev1_guard.py`) and the AI reviewer all use the rows below.

The shape follows the severity bars used for AI-system bug triage (the MSRC AI Bug Bar
is the generic reference): impact on confidentiality, integrity and availability decides
the level, and a row names the failure, not the fix.

## Levels

| Level | Meaning | Gate | Fix within |
| ----- | ------- | ---- | ---------- |
| SEV-1 | Real capital at risk, secrets exposed, ledger or tax data lost or wrong, or a safety control bypassed | Blocks merge and release | 24 h |
| SEV-2 | A wrong legal or money output, or a lost audit signal, with no capital loss yet | Blocks merge | 72 h |
| SEV-3 | Degraded behaviour or wrong non-money data; a human notices and works around it | Fix in the next batch | 7 d |
| SEV-4 | Cosmetic, copy, minor visual | Next sprint | none |

A row's level is its default. Raise it one level when the path can reach a real
exchange; lower it one level only when the Code Owner agrees in the issue.
If you cannot tell whether a path reaches a real exchange, treat it as if it can.

## Security rows (S1-S12)

| Row | Failure | Default |
| --- | ------- | ------- |
| S1 | A real-money order is sent without the typed confirmation (invariant I-08), or a test or dry-run flow can reach a real exchange | SEV-1 |
| S2 | A secret (key, token, webhook URL) reaches the repo, a log, a CI artifact or a test fixture | SEV-1 |
| S3 | An exchange key with more scope than the job needs (withdraw, trade where read-only is enough) is accepted or stored | SEV-1 |
| S4 | A write path on `serve` or the console is reachable without authentication, or the read-only console gains a write | SEV-1 |
| S5 | An LLM output decides, sizes or places a trade, or untrusted text (news, PR comment, exchange message) steers an agent action | SEV-1 |
| S6 | Sealed out-of-sample data (from 2025-09-01) is read by a backtest, a tuning step or a report | SEV-1 |
| S7 | The ledger or audit trail can be edited, truncated or reordered without a recorded event | SEV-1 |
| S8 | Personal data (tax identity, addresses, account numbers) appears in a log, metric or error message | SEV-2 |
| S9 | Untrusted PR code runs on a self-hosted runner with secrets or host access | SEV-1 |
| S10 | A dependency, action or image is unpinned, unverified or known-vulnerable on a deployed path | SEV-2 |
| S11 | Data from an exchange or data vendor is used in an order or tax path without validation | SEV-2 |
| S12 | A safety control (kill switch, risk cap, alert, drift check) can be disabled or bypassed silently | SEV-1 |

## Functional rows (F1-F12)

| Row | Failure | Default |
| --- | ------- | ------- |
| F1 | Ledger or portfolio state is lost or corrupted by a restart, crash or migration with no rollback | SEV-1 |
| F2 | Lots are selected in the wrong order (FIFO by `opened_at`, then `lot_id`) or a realized gain is wrong | SEV-1 |
| F3 | A tax figure, invoice or filing artifact is wrong (including the two-month repurchase rule) | SEV-1 |
| F4 | A position breaks the sizing rules: 1% risk, 5% per-pair cap, 15% exposure | SEV-1 |
| F5 | A trade or ledger row is booked twice (concurrent cycles, replay, retry) | SEV-1 |
| F6 | `serve` halts or skips bars with no alert | SEV-2 |
| F7 | A stale or missing bar is treated as fresh, or an exit is skipped on a stale book | SEV-2 |
| F8 | A backtest uses look-ahead (a stop raised and tested on the same bar, a future bar in a signal) or breaks the pre-registration | SEV-2 |
| F9 | Fees, slippage or fills are modelled differently from the engine that trades | SEV-2 |
| F10 | A bar boundary, DST change or timezone error shifts a signal, a day count or a tax year | SEV-2 |
| F11 | Portfolio and exchange drift apart and nothing reports it | SEV-2 |
| F12 | The console, a report or a brief shows a wrong money figure | SEV-3 |

## Using it

- An issue or PR that fixes a defect names its row in the body, for example "Row F5".
- A test that guards a SEV-1 row carries `# SEV-1: <row> <one-line failure>` above the
  test function. The plugin fails the session when such a test is skipped or xfailed.
- Rows can be added by PR to this file. Renumbering a row breaks existing annotations,
  so append instead.
