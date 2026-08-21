# ADR-001: The LLM supervises; it does not trade

**Date**: 2026-08-20
**Status**: Accepted
**Accepted**: 2026-08-21
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: the initial design, in which Claude placed orders through MCP and hooks constrained it

## Context

The original plan was an agentic one: connect Claude Code to Binance's Agent OS MCP
server (launched 2026-08-20) and let the model decide and place trades, with permission
rules and a guard hook bounding the damage. Research into the evidence base changed
this.

## Decision

A **deterministic engine** owns all strategy and execution decisions. **Claude acts as
supervisor**: reconciliation, reporting, anomaly detection, analysis, and build partner.
Claude never generates trade decisions, and connects to the exchange through read-only
tools only.

## Why

**LLMs perform badly as autonomous traders, and the failure mode is specifically costly
at small ticket sizes.**

- *Nof1 Alpha Arena* (Oct 18 – Nov 3 2025), the only live real-money contest: six
  frontier models, $10,000 each, identical prompts, no human intervention. Four of six
  lost money. GPT-5 finished down roughly 63%. Win rates ran 25–30%. The organisers'
  own diagnosis was that P&L was dominated by trading costs — the agents overtraded and
  took quick, tiny gains that fees erased.
- *StockBench* (arXiv:2510.02209, 2025), on contamination-free out-of-sample data: most
  LLM agents struggled to beat buy-and-hold. Strength on financial knowledge tasks did
  not transfer to trading.
- *FINSABER* (arXiv:2505.07078): over ~20 years and 100+ symbols, reported LLM
  advantages deteriorated sharply; the models were too conservative in bull markets and
  too aggressive in bear markets.
- *The Memorization Problem* (arXiv:2504.14765): GPT-4o recalls exact historical closing
  prices with under 1% error, so any backtest overlapping the training window measures
  retrieval, not prediction. Instructions to ignore the data did not work.
- *Prompt injection* is OWASP's top LLM vulnerability and has been demonstrated against
  trading agents specifically (arXiv:2512.02261). An agent that reads market commentary
  is ingesting text it does not control.

**Overtrading is the specific danger for this project.** Binance spot costs ~0.1% per
side, so a round trip is ~0.2% before spread. At $10–100 tickets, an agent inclined to
trade frequently converts the entire edge into fees. A deterministic rebalancer trades
a handful of times a year by construction.

**Spanish tax compounds it.** Every crypto-to-crypto swap is a taxable event requiring
FIFO cost basis in euros. High trade counts mean an accounting burden that grows without
bound, and raise the risk of reclassification as *actividad económica* (general rates up
to 47% rather than the savings base).

## Consequences

- The strategy must be expressible as rules. Threshold rebalancing was chosen partly
  because it is: target weights, a band, trade on breach.
- Claude's value moves to where it is genuinely strong — building the system, reading
  the ledger, reconciling state, explaining what happened, spotting anomalies.
- MCP tools are read-only. Order-placing tools stay in the deny list.
- The permission rules in `.claude/settings.json` enforce this, not `CLAUDE.md`.
  Instructions shape what Claude attempts; permission rules decide what it can do.

## What would change this decision

Credible evidence that LLM trading agents beat a passive benchmark net of costs, out of
sample, over a period containing more than one market regime. The current evidence is
the opposite, and the live-money evidence is the most damning of it.

## Reference

Full research: `docs/research/2026-08-feasibility.md`
