# The operator sees shadow P&L under a binding no-change rule

**Date**: 2026-10-03
**Author**: AI Bot on behalf of the Code Owner
**Related**: `docs/experiments/swing-mvp1/pre-registration-v1.md` §4, §10, §11, §13, §16;
[2026-10-02 forward testing during the OOS window](2026-10-02-forward-testing-during-oos-window.md)
(D-8 guardrail); [ADR-012](../architecture/adr-012-continuous-ai-evaluation.md) D-06;
[ADR-014](../architecture/adr-014-pre-registration-binds-real-capital.md);
GitHub issues #319, #353, #355, #378 (ADR-015, pending) and #385

## Decision

The Code Owner chose "Go full" at 13:18 UTC on 2026-10-03, on the commitment
they stated in their own words: "If we commit to not change the strategy on the
time period define can we go full?"

The Code Owner sees shadow P&L, realized and unrealized, gross and after
Spanish tax (FIFO), in the operator console (`hermes status`,
`hermes positions`, `hermes tui`) and in the Telegram `/status` and
`/positions` replies they request. In return, the Code Owner is bound by the
rule below until v1 reaches OOS certification or a terminal state.

## Binding rule

1. **No strategy changes.** Nothing in pre-registration v1 §3, §5, §6, §9 or
   §10 changes before certification (universe, strategy spec, sizing, fee and
   slippage model, metrics and thresholds, kill criteria), whatever the
   reason. Any change starts v2 under the D-8 guardrail. The period up to that
   change counts as in-sample, and v2 certifies on later data.
2. **Only rules stop trading.** Shadow trading halts only for (a) a §10 kill
   criterion computed by the engine, or (b) an operational fault: outage, bad
   data, an engine bug, an exchange incident or a security issue. Every manual
   pause or resume gets a lab-notebook entry naming the operational reason.
   Pausing, resuming or abandoning v1 because of P&L is a deviation (optional
   stopping).
3. **Certification is mechanical.** The OOS certification runs once, on or
   after 2027-01-01, through the code-enforced gates (#355). Its pass or fail
   stands. The operator sign-off for `LIVE_APPROVED` covers operational
   readiness only and can't overrule a failed gate.
4. **Automation stays blind.** ADR-012 D-06 is unchanged. The supervisor desk
   (#365), the challenger lab (#361), every ADR-012 loop and the MCP tools
   (#34) see no P&L, equity, position value, Sharpe, drawdown or win rate
   before certification. Pushed notifications, Slack digests and reports also
   stay blind under #319, because they are durable records AI sessions can
   read. P&L appears only in output the Code Owner pulls on demand.
5. **v2 can't reuse this window.** Any later pre-registration treats
   2025-09-01 to 2026-12-31 as in-sample.

## Integrity rule 1.8 and §16

This was decided and recorded before any shadow P&L existed: no simulated fill
has happened yet (#353), so no OOS outcome informed it. After that point P&L is
visible, but the binding rule removes every decision it could inform: no
strategy change, no discretionary stop, mechanical certification. Rule 1.8
treats a request to see the equity curve as a trigger for the integrity
checker to flag. This record is that flag, answered with an explicit, logged
Code Owner decision.

§16 and §11 apply to edits of the signed file and to the §11 deviation list.
This decision edits neither: the signed pre-registration and its hash stay
exactly as signed, so it is not v2. Where a version label is wanted, it is
v1.1, with identical windows (OOS 2025-09-01 to 2026-12-31), spec, thresholds
and certification date. A v1.1 label never moves the OOS window.

## Context

The blind on forward results came from the #319 ruling (no P&L in
notifications) and integrity rule 1.8, which are operating rules around the
experiment. Nothing in the signed pre-registration requires the operator to be
blind. The D-8 guardrail, confirmed on 2026-10-03, already rests v1's
integrity on discipline: shadow runs inside the OOS window, and shadow
observations must not motivate parameter changes before certification. Seeing
P&L makes that discipline harder to keep, but it is not a different kind of
risk. If the strategy can't change and every stop is rule-based, seeing results
can't move the one-shot OOS test.

Alternatives considered:

- **Keep the operator blind until certification**: P&L and tax views on
  backtest data only. Rejected by the Code Owner, who wants to see how trading
  is going.
- **Invalidate v1 and pre-register v2 with a new OOS window**: moves real
  capital to about 2028 for a risk the binding rule already contains.
  Rejected.

## What the console shows

Every money figure reads gross, then trading fees, then estimated tax (FIFO
lots, ADR-002 savings-base scale, losses in the year offsetting gains), then
net. This applies per open position (tax if sold now) and to closed trades in
the tax year. Estimates are labelled as estimates, not a tax filing.

## Consequences

- The experiment's integrity claim rests on the Code Owner keeping this rule,
  as it already did under D-8. The lab notebook makes each manual action
  auditable.
- The `pnl_visible` gate becomes a surface allowlist (operator console and
  Telegram pull replies) instead of a date. AI-facing projections keep the
  ADR-012 D-06 allowlist and its blindness test.
- This record and its lab-notebook entry must merge before the first
  simulated fill (#353). Shadow P&L appears on no surface before then.
