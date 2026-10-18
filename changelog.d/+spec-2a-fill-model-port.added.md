- **spec-2a FillModelPort + ExchangePort** (`src/hermes/ports/fill_model.py`,
  `src/hermes/ports/exchange.py`, `src/hermes/domain/trading/`,
  `openspec/changes/spec-2a-fill-model-port/`): introduces `FillModelPort` —
  a single-method `@runtime_checkable` Protocol whose `fill_order` signature
  is shared identically by backtest, shadow, and live modes, enforcing the
  same-code-path invariant at the structural level (mode divergence is wired
  at the composition root, never inside the port). Replaces the empty 19-LOC
  `ExchangePort` stub with a full six-method Protocol contract
  (`place_market_order`, `place_limit_order`, `cancel_order`, `get_orderbook`,
  `get_balance`, `get_fees`), closing Issue #199. Domain types `Order`,
  `Fill`, `Rejection`, and `OrderSide` are introduced as pure domain value
  objects. The fill result uses a native `Fill | Rejection` discriminated
  union rather than a `Result` monad — both are expected business outcomes,
  not errors. Structural-conformance harnesses (54 tests) prove both
  Protocols are implementable via minimal stubs and verify the absence of
  mode-selection parameters. Follow-ups: `OrderbookWalkFillModel` concrete
  fill implementation (Spec 2b), `SimulatedExchangeAdapter` (Spec 2c),
  Binance/Kraken adapter migration to the new `ExchangePort` shape.
