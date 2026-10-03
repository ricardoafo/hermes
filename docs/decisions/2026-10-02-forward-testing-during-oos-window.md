# Forward testing stays available inside the OOS window

**Date**: 2026-10-02
**Author**: AI Bot on behalf of the Code Owner
**Related**: `docs/experiments/swing-mvp1/pre-registration-v1.md` section 4
(data windows), `src/hermes/application/strategy_protocol/data_split_enforcer.py`,
ADR-011 (shadow mode), GitHub issues #307 and #301,
`openspec/changes/307-live-bar-stream/change.md` (D-8),
[2026-10-03 operator sees shadow P&L](2026-10-03-operator-sees-shadow-pnl.md)
(binding no-change rule that builds on this guardrail)

## Decision

Forward testing (shadow mode on live data) and backtesting on in-sample
history both stay available, and a forward feed is not wrapped by
`DataSplitEnforcer`. The Code Owner said: "We should start back and forward
testing when possible, so we need to have this available too."

Guardrail, confirmed as written by the Code Owner on 2026-10-03: shadow observations made inside the OOS window must not motivate parameter changes before OOS certification; if they do, the new pre-registration treats that period as in-sample and certifies on later data.

Confirmation (2026-10-03, MVP1 readiness review): the Code Owner said
"confirm the guardrail as written. It is what keeps the 2027 OOS test valid
while shadow runs". See ADR-014 for the real-capital gate this protects.

## Context

Pre-registration v1 fixes the OOS window at 2025-09-01 to 2026-12-31 and says
the code physically refuses to read it outside certification mode.
`DataSplitEnforcer` implements that by raising on any bar opening inside the
window. Today is 2026-10-02, so every live bar falls inside it, and so does
the 1d warm-up, which starts in December 2025. `hermes serve` wires no
enforcer.

The rule exists to stop tuning on held-out history. A forward feed holds no
future data: each bar is observed once, as it closes, and nothing in it was
available when parameters were set. Warm-up only primes indicator buffers and
produces no signal.

Alternatives considered:

- **Wrap the feed with the enforcer**: blocks every bar until 2027-01-01 and
  makes forward testing impossible. Rejected.
- **Run the feed in certification mode**: defeats the guard. Rejected.
- **Amend the pre-registration to exempt the shadow phase**: edits a frozen
  document. Rejected here; the guardrail above is the proposed substitute.

## Consequences

- Backtests keep the enforcer on historical reads, so the OOS window is still
  unreadable from history.
- The experiment's integrity claim now rests on discipline: the guardrail
  above, which the Code Owner confirmed on 2026-10-03.
- Reversing this decision touches the experiment's integrity claim, so the
  cost of reversal is high. Revisit before OOS certification starts on
  2027-01-01.
