# Change: 319-shadow-blind-notifications

**SDD change**: `319-shadow-blind-notifications`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/319-shadow-blind-notifications/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Until v1 out-of-sample certification, no notification may show how the
strategy is doing (ADR-012 ruling 1, option B; integrity rule 1.8). This
change adds a single `ReportingPhase` setting that defaults to
pre-certification and makes the Slack daily trade digest render blind under
it: activity counts and notional stay, the winners/losers counts and the
per-pair P&L CSV are withheld. Unblinding is an explicit constructor argument
on the notifier adapter that logs a warning. Main risk: a future template
that forgets to register as phase-aware; the shared field list and the
render-and-scan test pattern exist so #309 and later reports can reuse them.

## Scope

### In

- `src/hermes/application/messages/reporting_phase.py`: `ReportingPhase`,
  `DEFAULT_REPORTING_PHASE`, `OUTCOME_FIELD_NAMES`, `OUTCOME_WITHHELD_NOTICE`.
- `src/hermes/application/messages/slack.py`: `slack_template(event, *, phase)`
  and a phase-aware registry the dispatcher consults first; the daily digest
  is its first entry.
- `src/hermes/adapters/notifier/slack_adapter.py`: `reporting_phase` field
  (default pre-certification) passed to every render; a warning is logged when
  built `CERTIFIED`.
- Tests: blind digest renders with populated P&L (field-name scan, property
  test over P&L amounts), wire-level test that no upload happens, unblinding
  log test; preview CLI now previews the blind digest.

### Out

- Sunday digest P&L lines (ADR-008 §4-5) and the M-09 / M-10 reports: neither
  exists in code yet (`rg -n "M-09|M-10|sunday" src` has no match). They must
  register as phase-aware templates when they land; M-09 / M-10 render pass or
  fail only.
- The new signal messages of #309 (its spec R-0 carries the same rule and can
  reuse `ReportingPhase` and `OUTCOME_FIELD_NAMES`).
- Telegram templates: none carries an outcome figure today.

## Decisions

### D-1: Phase is a code-level default plus an explicit adapter argument

- **Decision**: `DEFAULT_REPORTING_PHASE = PRE_CERTIFICATION`; templates and
  the Slack adapter default to it, and only an explicit `CERTIFIED` argument
  unblinds.
- **Alternatives**: (a) YAML / env setting: a stray `HERMES_` variable could
  unblind silently, which breaks fail-closed. (b) Date check against
  2027-01-01: templates are pure and must not read the clock; certification is
  a decision, not a date.
- **Rationale**: Fail-closed by default; unblinding is a reviewed code change at
  the composition root plus a logged warning at startup.
- **Cost of reversal**: LOW.

### D-2: Phase-aware registry beside the existing one

- **Decision**: `_PHASE_AWARE_TEMPLATES` maps event types to
  `(event, phase)` renderers; short templates keep their one-argument shape.
- **Alternatives**: change every renderer signature (churn with no gain).
- **Cost of reversal**: LOW.

### D-3: Winners/losers counts are outcome figures

- **Decision**: The blind digest drops the "Top winners / Top losers" counts.
- **Rationale**: the counts are a win-rate proxy.

## Requirements

### R-1: The digest is blind by default

- **A-1.1**: `uv run pytest tests/unit/application/messages/test_slack.py -q`
  passes, including `TestDailyTradeDigestBlind` (no outcome field name, no P&L
  value for any amount, no attachment).
- **A-1.2**: `uv run pytest tests/unit/adapters/notifier/test_slack_adapter.py -q`
  passes, including the test that a default adapter makes no upload call.

### R-2: Unblinding is explicit and logged

- **A-2.1**: `TestReportingPhaseLogging` passes: a `CERTIFIED` adapter logs
  `slack_notifier_unblinded` once; the default adapter logs nothing.

## Design

```python
class ReportingPhase(StrEnum):
    PRE_CERTIFICATION = "pre_certification"
    CERTIFIED = "certified"

    @property
    def shows_outcomes(self) -> bool: ...


def slack_template(
    event: DomainEvent, *, phase: ReportingPhase = DEFAULT_REPORTING_PHASE
) -> SlackMessage: ...
```

## Test plan

1. Acceptance tests above pass.
2. `uv run pytest tests/unit -q` passes (1777 passed locally).
3. CI green on the PR.

## Risks

- **A new outcome-bearing template skips the phase-aware registry**: it would
  render unblinded. Mitigation: the module docstring states the contract and
  the reusable `OUTCOME_FIELD_NAMES` scan is the test pattern #309 R-0 asks for.
- **`BalanceLow` shows a balance amount**: it is integration health (exchange
  funding) and routed to Telegram; left visible per the issue's allow list.

## Dependencies on other changes

- ADR-012 (#310) ruling 1. #309 depends on this merging.
