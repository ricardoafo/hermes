- **`hermes serve` composition root** (`src/hermes/entrypoints/serve.py`):
  replaces the "not yet implemented" stub with a real composition that wires
  `HermesConfig` → `SqliteLedger` (runs migrations on construct) + `WallClock`
  + `BinanceHistoricalAdapter` + `SwingStrategyAdapter` + `EventBus` + an
  optional `TelegramAdapter` (best-effort — absent credentials degrade to
  `None`). `run()` awaits an `asyncio.Event` and closes the SQLite connection
  + notifier in a `finally` block. `install_signal_handlers` binds SIGTERM +
  SIGINT to the shutdown event with a Windows fallback via `signal.signal`.
  OrderRouter + SimulatedExchangeAdapter wiring intentionally left for a
  follow-up Phase-2 wave. CLI accepts `--config-dir` and `--db` flags.
