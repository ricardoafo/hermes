### Added

- **spec-2b OrderbookWalkFillModel** (`src/hermes/adapters/fills/orderbook_walk.py`,
  `src/hermes/adapters/fills/__init__.py`,
  `openspec/changes/spec-2b-orderbook-walk-fill-model/`): delivers the
  concrete `FillModelPort` implementation that drives both backtest (synthetic
  orderbook from historical bars) and shadow (live L2 book) phases — the same
  class, same code path, no `if backtest_mode:` branching (ADR-011 D-02
  same-code-path invariant). The fill model walks orderbook levels by price,
  computes a VWAP across consumed levels, and applies optional basis-point
  slippage (5 bps entries / 15 bps stop-exits, pre-reg §6.2) only when
  `apply_slippage=True` at the composition root. Limit orders are rejected
  with `PRICE_FILTER` when the best price is unreachable; thin books return
  `INSUFFICIENT_DEPTH`. A deterministic `random.Random` instance is accepted
  for reproducible chaos and property testing. Anti-Zipline invariant enforced:
  market orders fill at the first orderbook level (= next bar's open as wired
  by the composition root), never at the current bar's close (SA-2 AP-1).
  Test suite (32 tests): port conformance, anti-Zipline, quantity bound
  property, VWAP multi-level, slippage directional checks, fee taker-rate
  validation, deterministic replay, rejection matrix (PRICE_FILTER /
  INSUFFICIENT_DEPTH / naive-clock), and Fill timestamp contract.
  Closes #230.
