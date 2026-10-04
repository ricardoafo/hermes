- **Kraken failover fees** (`src/hermes/adapters/exchanges/kraken_spot.py`):
  Kraken stopped publishing fee tiers in its public `AssetPairs` reply, so the
  Kraken adapter now reads the account's own maker and taker fee from the
  private, read-only `TradeVolume` call. The fee contract test skips without a
  Kraken key. Closes #599.
