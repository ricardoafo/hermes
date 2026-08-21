# 04 — Exchange Adapters

**Parent ADR**: [README.md](./README.md)
**Status**: Accepted
**Accepted**: 2026-08-21

## Question this section answers

How does `ExchangePort` abstract Binance/Kraken/etc. without leaky abstractions? What methods does it expose, what errors does it raise, how do we test it? Where does EUR pricing come from?

## The port — semantic interface, NOT lowest common denominator

```python
class ExchangePort(Protocol):
    """Adapters translate exchange peculiarities to this semantic interface."""

    # ── Read-only ──────────────────────────────────────────────
    def get_portfolio(self) -> Portfolio: ...
    def get_prices(self, pairs: list[Pair]) -> dict[Pair, Price]: ...
    def get_candles(self, pair: Pair, interval: Interval, since: datetime) -> list[Candle]: ...
    def get_trading_rules(
        self, pair: Pair
    ) -> TradingRules: ...  # MIN_NOTIONAL, lot step, tick size
    def get_fees(self, pair: Pair) -> FeeSchedule: ...
    def list_open_orders(self) -> list[OrderInfo]: ...
    def get_order_status(self, order_id: OrderId) -> OrderStatus: ...
    def get_eur_rate(self) -> Decimal: ...  # EUR/USDT for FIFO pricing

    # ── Write / trading ────────────────────────────────────────
    def place_market_order(self, pair: Pair, side: Side, qty: Decimal) -> OrderFill: ...
    def place_stop_loss(self, pair: Pair, qty: Decimal, stop_price: Price) -> OrderId: ...
    def cancel_order(self, order_id: OrderId) -> None: ...
    def modify_stop_loss(self, order_id: OrderId, new_stop_price: Price) -> None: ...

    # ── Streaming ──────────────────────────────────────────────
    def subscribe_price_stream(
        self, pairs: list[Pair], on_tick: Callable[[Tick], None]
    ) -> Subscription: ...
```

## Value objects the port produces

`OrderFill` enforces EUR pricing from row 1 — the adapter fills this in, domain never handles currency conversion:

```python
@dataclass(frozen=True)
class OrderFill:
    order_id: OrderId
    pair: Pair
    side: Side
    qty: Decimal
    price_native: Decimal  # e.g., USDT
    price_eur: Decimal  # REQUIRED — populated by adapter, feeds FIFO
    eur_rate: Decimal  # EUR/USDT at fill time
    fee_native: Decimal
    fee_eur: Decimal
    executed_at: datetime
```

## Domain errors — adapter maps exchange codes to these

```python
class ExchangeError(Exception): ...


class InsufficientBalance(ExchangeError): ...


class OrderRejectedForMinNotional(ExchangeError): ...


class OrderRejectedForLotStep(ExchangeError): ...


class ExchangeTemporarilyUnavailable(ExchangeError): ...  # retry-exhausted


class InvalidPair(ExchangeError): ...


class RateLimitExceeded(ExchangeError): ...


class AuthenticationFailed(ExchangeError): ...  # API key expired → alert user
```

## Leaky abstraction risks (why the port is semantic)

Different exchanges vary in:

| Concern                | Binance        | Kraken                       | Coinbase        |
| ---------------------- | -------------- | ---------------------------- | --------------- |
| Symbol format          | `BTCUSDT`      | `XBT/USD`                    | `BTC-USDT`      |
| Stop-loss types        | market + limit | market + limit + take-profit | limit only      |
| Order type IOC support | yes            | yes                          | limited         |
| WebSocket envelope     | flat JSON      | nested with channel          | protobuf option |
| Rate limit model       | weight-based   | counter-based                | request-count   |
| Timestamp format       | ms integer     | μs integer                   | ISO string      |
| Decimal precision      | 8 places       | 8 places                     | 8 places        |

Each adapter TRANSLATES these to the port's terms. Peculiarities NEVER leak into domain.

## Retry policy — asymmetric

- **Idempotent reads** (get_portfolio, get_prices, get_candles): retry 3× with exponential backoff (100ms → 200ms → 400ms)
- **Non-idempotent writes** (place_market_order): **NEVER auto-retry.** If we don't get a definitive response, surface to user + reconcile via `list_open_orders()`. **Reason: double-fill risk** — silently retrying a "maybe filled" market order can double the position.
- **Cancellations** (cancel_order, modify_stop_loss): retry-safe

## Rate limiting — defensive token bucket per adapter

```python
class BinanceAdapter:
    def __init__(self):
        self.limiter = TokenBucket(
            capacity=5000,  # buffer below Binance's 6000/min
            refill_per_second=83,  # 5000/60
        )
```

At hermes scale we're 4 orders of magnitude below limits. The guard is defensive.

## EUR pricing source — decision

### Selected: Binance BTC/USDT price × ECB EUR/USDT reference rate

Alternatives considered:

- **Binance BTC/EUR pair (native)** — rejected: thin books, EUR-quoted crypto pairs are shallow globally, wider spreads = worse fills
- **Coingecko aggregated EUR rate** — rejected: third-party dependency without adding rigor

**Method**: `get_eur_rate()` fetches ECB daily rate + caches 24h. Weekend trades use last-known ECB rate + record timestamp mismatch in trade row for audit clarity. Hacienda accepts this.

## Configuration per exchange

```yaml
# config/exchanges/binance.yaml
base_url: https://api.binance.com
ws_url: wss://stream.binance.com:9443/ws
timeouts:
  connect_ms: 5000
  read_ms: 10000
retries:
  max_attempts: 3
  base_backoff_ms: 100
rate_limit:
  request_weight_per_minute: 5000   # buffer below 6000
symbols_format: "{base}{quote}"     # BTCUSDT
credentials:
  api_key_ref: ${credential:binance_api_key}
  api_secret_ref: ${credential:binance_api_secret}
fee_snapshot:
  spot_taker_bps: 10   # 0.10%
  spot_maker_bps: 10
```

## Testing strategy — three levels

1. **Unit tests** — mock `ExchangePort` in domain/application tests. Fast, no I/O, deterministic.
2. **Contract tests per adapter** — hit **Binance TESTNET** (real API, fake money). Every port method verified against real exchange, every error mapping tested.
3. **Integration tests** — full flow against testnet, one asset, tiny amounts.

**Never** test with real money against production Binance in CI. Ever.

## Alternatives considered

| Option                                                                     | Why rejected                                                                                            |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **CCXT library** (unified crypto exchange abstraction)                     | Massive dependency, lowest-common-denominator API, breaks the semantic-port principle. We keep control. |
| **Separate ports per capability** (ReadPort + TradingPort + StreamingPort) | Over-decomposition at our scale. One coherent port is easier to reason about.                           |
| **Async everywhere**                                                       | <100 req/day. Sync httpx is simpler. WebSocket streaming uses its own thread.                           |

## Cross-references

- [01-code-organization.md](./01-code-organization.md) — where BinanceAdapter/KrakenAdapter live
- [02-data-flow.md](./02-data-flow.md) — how RebalanceUC calls ExchangePort methods
- [03-persistence-schema.md](./03-persistence-schema.md) — where OrderFill data lands
- [05-signal-engines.md](./05-signal-engines.md) — Donchian uses `get_candles` for indicator calcs
- [09-deployment.md](./09-deployment.md) — Binance credentials via systemd LoadCredentialEncrypted
