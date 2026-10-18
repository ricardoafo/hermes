- **Spec 5 — swing strategy adapter** (`src/hermes/ports/strategy.py`,
  `src/hermes/domain/strategy/`): first signal-generation layer of the
  swing-MVP1 experiment. `SwingStrategyAdapter` implements the new
  `StrategyPort` with 4h and 1D bar buffers and an entry/exit state machine
  over five engines bound to pre-registration v1 §5: 4h Donchian(20) breakout,
  4h Chandelier(22, 3.0) trailing stop, 4h ADX(14), 1D EMA(200) regime filter
  and a Wilder ATR helper. Adds the `StrategySignal` value object and the
  `StrategyConfig` dataclass; the Kelly `f_star_estimate` stays `None` until
  enough out-of-sample trades exist. Closes #235.
