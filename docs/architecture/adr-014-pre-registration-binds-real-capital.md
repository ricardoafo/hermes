# ADR-014: Real-capital timing follows the signed pre-registration — no live capital before the one-shot OOS certification, not before 2027-01-01

**Date**: 2026-10-03
**Status**: Accepted — decided by the Code Owner on 2026-10-03 (MVP1 readiness review); confirmed at PR review
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: the real-capital dates in [ADR-004](adr-004-exchange-decision.md) (Decision item 3, "before Nov-2026 real-capital deployment", and the matching "before Nov 2026 real-capital deployment" in Why) and in [ADR-006](adr-006-versioning-strategy.md) §1 pre-release tags (shadow "Aug-Nov 2026"; `1.0.0` for €1000+ real capital "end 2026-Q4"). Nothing else in either ADR changes.

---

## Context

ADR-004 and ADR-006 were accepted on 2026-08-21, before the Swing MVP1
experiment had a protocol. Both carry calendar dates for real capital:

- ADR-004 asks for Kraken KYC "before Nov-2026 real-capital deployment".
- ADR-006 places shadow mode in Aug-Nov 2026 and targets `1.0.0`, the first
  release cleared for €1000+ real capital, at end 2026-Q4.

On 2026-08-25 the operator signed
[pre-registration v1](../experiments/swing-mvp1/pre-registration-v1.md)
(content SHA256 `8d73e99e…3433`, verification protocol in its §16). It sets a
different bar:

- §4: the OOS window is 2025-09-01 to 2026-12-31 and certification cannot
  begin before 2027-01-01.
- §7: `LIVE_APPROVED` requires `OOS_CERTIFIED`, which is a one-shot test on
  or after 2027-01-01.

So two documents disagreed on when real money may move, and the GitHub MVP
issues (#141, #142) added a third reading. On 2026-10-03 the Code Owner
ruled: "the pre-registration binds", and asked for the ADR dates to be
superseded by a new ADR because accepted ADRs cannot be edited (ADR-009).

## Decision

1. **The signed pre-registration v1 is the binding gate for real capital.**
   Where an ADR, decisions entry, roadmap or issue gives a different date or
   bar, the pre-registration wins.
2. **No live capital before the one-shot OOS certification.** No order that
   commits real funds is placed for the Swing MVP1 strategy until it reaches
   `LIVE_APPROVED` (pre-registration §7: `OOS_CERTIFIED` plus operator
   sign-off).
3. **The OOS certification does not run before 2027-01-01** (pre-registration
   §4 discipline gate). It runs exactly once.
4. **Acceptance criteria are the pre-registration's, unchanged.** H1 is
   accepted iff DSR on OOS > 0.7 and every metric in §9 passes: the primary
   rows (DSR on OOS > 0.7, bootstrap 95% CI lower bound on OOS Sharpe > 0,
   OOS trade count ≥ 30) and all secondary rows. Kill criteria are §10. This
   ADR restates none of them as new values; §1, §7, §9 and §10 are the source
   of truth.
5. **Dates superseded.** ADR-004's "before Nov-2026" becomes "before the
   first `LIVE_APPROVED` capital deployment". ADR-006's SemVer scheme stands;
   only its calendar changes: `1.0.0-shadow.N` covers shadow mode for as long
   as it runs, and `1.0.0` (first release cleared for real capital) is cut
   no earlier than the `LIVE_APPROVED` sign-off, so no earlier than
   2027-01-01.

## Why

- **The pre-registration is the anti-p-hacking mechanism.** Its value comes
  from being fixed before the data is seen. A calendar target that lets
  capital go live before certification would make the protocol advisory.
- **One bar, not three.** Two ADRs and two issues giving different dates is
  how a deadline quietly overrides a protocol. Naming one binding document
  removes the ambiguity.
- **ADR-009 immutability.** The old ADRs stay readable in their original
  context; this ADR records what changed and why.

Alternatives considered:

- **Edit ADR-004 and ADR-006 in place.** Rejected; violates ADR-009.
- **Let the GitHub MVP issues define the bar.** Rejected by the Code Owner;
  issues are tracking, not protocol.
- **Amend the pre-registration to match the ADR dates.** Rejected; any
  deviation from a signed pre-registration is a new experiment
  (pre-registration §11).

## Consequences

### Positive

- One unambiguous answer to "when can real money move".
- Shadow mode and backtesting continue without a calendar pushing toward
  live capital.

### Negative

- Real capital moves later than ADR-004 and ADR-006 planned (2027 at the
  earliest, not Nov-2026).

### Neutral

- Kraken KYC, the failover runbook and the SemVer scheme are unchanged;
  only their deadlines move.
- Forward testing inside the OOS window stays allowed under the guardrail in
  [2026-10-02 forward testing during the OOS window](../decisions/2026-10-02-forward-testing-during-oos-window.md),
  which the Code Owner confirmed on 2026-10-03.

## What would change this decision

- A new signed pre-registration (v2 or later) with different windows or
  criteria. This ADR then follows the newest signed version, and a
  follow-up ADR records it.
- The experiment is killed under pre-registration §10; real-capital timing
  then waits on the next experiment's pre-registration.

## Cross-references

- [Pre-registration v1](../experiments/swing-mvp1/pre-registration-v1.md) §1, §4, §7, §9, §10, §11
- [ADR-004](adr-004-exchange-decision.md) — exchange posture (dates superseded here)
- [ADR-006](adr-006-versioning-strategy.md) — versioning (pre-release calendar superseded here)
- [ADR-009](adr-009-documentation-strategy.md) — ADR immutability and supersession
- [ADR-011](adr-011-paper-trading-engine.md) — certification lifecycle in the engine
- [2026-10-03 operator sees shadow P&L](../decisions/2026-10-03-operator-sees-shadow-pnl.md) — binding no-change rule while the operator watches shadow P&L

## Pending

- Rewrite #141 and #142 to point at the pre-registration as the real-capital bar.
- Enforce the certification gates in code (Code Owner ruling, 2026-10-03).
