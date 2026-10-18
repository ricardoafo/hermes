# ADR-020: Read-only exchange query keys before MVP2 — ban trade-capable keys, allow one query-only key for contract tests

**Date**: 2026-10-06
**Status**: Accepted (merged in PR #800)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: in part, [ADR-019](adr-019-security-baseline-and-threat-model.md): Decision 2 and the "Until MVP2"
bullet of §Exchange API keys. Everything else in ADR-019 stands.

---

## Context

ADR-019 Decision 2 says no exchange key of any kind is stored in GitHub or on the VPS until MVP2. One key already
exists for a good reason. The daily contract test `tests/contract/test_kraken_public_fees.py` reads the account's own
Kraken fee tier through the signed, read-only `TradeVolume` call (#599). Kraken stopped publishing fee tiers in its
public reply on 2026-10-04, so this test is the only drift check on the fee the failover adapter uses. The key has
the Query Funds permission only, and the test skips when the key is missing.

Read literally, ADR-019 would delete that key and silently turn the fee check off. The conflict was found after
ADR-019 was approved but before it merged. On 2026-10-06 at 01:30 Madrid, Rick chose "Keep fee check": keep the
read-only key and ban only trade-capable keys. ADR-019 merged before the wording change landed. ADR-019 is Accepted,
so the change is this ADR (ADR-009 lifecycle, AGENTS.md rule 10).

## Decision

1. **No trade-capable exchange key is stored in GitHub or on the VPS until MVP2 needs it.** That covers any key with
   trade, withdraw, transfer, margin or futures rights, testnet included. The Binance mainnet and testnet secrets
   that `vps-bootstrap.yml` passes through stay deleted (#769).
2. **One read-only query key per exchange is allowed for contract tests.** It must have query permissions only, with
   no trade, withdraw or transfer right. It lives in the GitHub `prod` environment only, never at repo level (#768),
   and it is rotated on the #679 schedule. Today this is the Kraken key that the fee contract test uses.
3. **The read-only key is exempt from the IP lock.** The contract tests run on the CI runners, not on hermes-01, so
   locking the key to the VPS address would break the test. The query-only scope is what limits the damage of a
   leak: a leaked key can read the account but cannot move money.

## Why

- A leaked read-only key exposes balances and fee tier, nothing more. The fee drift check it enables guards the
  failover adapter's cost model, which a silent Kraken change would otherwise break.
- Banning trade-capable keys keeps the point of ADR-019 Decision 2: until MVP2, nothing stored anywhere can place an
  order or move funds.

## Consequences

### Positive

- The Kraken fee contract test keeps running daily.
- The rule reads the way it is enforced: by the key's permissions, not by whether any key exists.

### Negative

- One exchange credential stays stored before MVP2. It is read-only and environment-scoped, but it is still a
  secret to rotate.

### Neutral

- No code or workflow changes. The Kraken secrets move into the `prod` environment under #768 as already planned.

## What would change this decision

- MVP2 starts real trading: ADR-019 §Exchange API keys governs the trade key. The read-only key stays separate, or is
  replaced by the trade key's read access if the exchange allows one key with narrower scopes per caller.
- Kraken publishes fee tiers publicly again: drop the key and this exception.

## Cross-references

- [ADR-019](adr-019-security-baseline-and-threat-model.md) — superseded in part
- [`tests/contract/test_kraken_public_fees.py`](../../tests/contract/test_kraken_public_fees.py)
- Issues: #769 (key policy), #768 (environment scoping), #679 (rotation), #599 (fee contract test)
