# SA-0 Landscape Scouting: Paper-Trading & Backtest Systems

**Date**: 2026-08-24
**Author**: SA-0 sub-agent (general-purpose, sonnet)
**Engram topic key**: `research/paper-trading-sa0-landscape`
**Confidence**: HIGH on OSS inventory + failure evidence + Quantopian post-mortem + shortlist. MEDIUM on exchange testnet divergence bps + multi-strategy secondary sources.

---

## 1. MASTER INVENTORY TABLE

### Open-Source Python-Heavy Frameworks

---

Zipline / Zipline-Reloaded (ml4trading fork)

- URL: <https://github.com/stefan-jansen/zipline-reloaded> | <https://zipline.ml4trading.io/>
- Category: Backtest-only (live support minimal via separate Zipline-Live fork)
- Status: Original (Quantopian) abandoned 2020. Zipline-Reloaded by Stefan Jansen actively maintained (v3.1.1, July 2025)
- Adoption: Was Quantopian's core engine; 200k+ users historically; still used in ML for Algorithmic Trading book
- Time Model: Event-driven, bar-close (daily and minute resolution). Orders placed at bar close, filled at NEXT bar's close (not open) — documented in issue #2011 and #2364
- Multi-strategy: Single strategy per run; no native multi-strategy shared capital
- Language: Python

What it does well:

- Unparalleled equity factor research with Pipeline API for dynamic universe filtering
- Native dividend/split adjustment handling
- Clean Pythonic API for daily equity backtesting

What it does badly / cannot do:

- Crypto not supported natively (Catalyst fork died)
- Fill model: VolumeShareSlippage fills at next-bar CLOSE price, not open — this is WRONG. Orders should fill at next open (GitHub issue #2011)
- Tick-level simulation impossible
- Adjusted vs unadjusted price confusion (issue #490): Quantopian used unadjusted, Zipline used adjusted — divergence between platforms
- No meaningful paper-trading mode
- Installation nightmare on modern Python without forks

Notable failure modes:

- Issue #2011: `VolumeShareSlippage.process_order` fills at next bar's CLOSE, not OPEN — pure look-ahead bias in fill timing (<https://github.com/quantopian/zipline/issues/2011>)
- Issue #2364: "Instant execution at market open" — daily mode executes at bar close of NEXT day, not open (<https://github.com/quantopian/zipline/issues/2364>)
- Issue #490: Adjusted/unadjusted price divergence between Quantopian cloud and local Zipline (<https://github.com/quantopian/zipline/issues/490>)
- Issue #2635: Limit buy orders with daily data not working as expected

---

Backtrader

- URL: <https://www.backtrader.com/>
- Category: Both (backtest + live via broker adapters)
- Status: EFFECTIVELY ABANDONED. Original maintainer went into archive mode in 2023. Community forks patching Python 3.10+ compatibility. Not recommended for new projects as of 2026.
- Adoption: Large historical user base; still widely cited but shrinking community
- Time Model: Pure Python event-driven loop. Bar-close signal → fills at next bar open. No tick-level support.
- Multi-strategy: Yes, multiple strategies can share a broker/portfolio, but performance degrades badly at scale (single-threaded Python loop)
- Language: Python

What it does well:

- Elegant, expressive API
- Extensive documentation and tutorial archive
- Rich built-in indicator library
- Multi-asset support within a single run

What it does badly / cannot do:

- Single-threaded Python execution: "Optimization cycles become prohibitively slow at scale"
- No tick-level backtesting
- Fill model: OHLCV bar only; intrabar price movement is unknowable (ambiguity problem)
- Cheat-on-Open mode exists but is a workaround hack
- Python 3.10+ compatibility requires community patches
- Live trading bridges unreliable and mostly unmaintained

Notable failure modes:

- Bar ambiguity: When both stop-loss and take-profit fall within the same bar, the engine must GUESS which was hit first. A 2026 study (Claeys, SSRN 6240638) quantified this for scalping: 18.47% of all NQ futures bars are ambiguous; gap between best-case and worst-case assumptions reaches 3,695 NQ points per 1,000 trades
- "Cheat-on-open" is officially documented as a workaround (<https://www.backtrader.com/docu/cerebro/cheat-on-open/cheat-on-open/>) — revealing the core bar-close execution timing problem

---

QuantConnect LEAN

- URL: <https://github.com/QuantConnect/Lean> | <https://www.lean.io/>
- Category: Both (backtest + paper trade + live)
- Status: Actively maintained. ~180 engineers. Powers 300+ hedge funds. v3+ actively developed.
- Adoption: Large institutional + retail user base; cloud platform with millions of backtests
- Time Model: C# event-driven core. Tick, second, minute, hour, daily resolutions. True event stream.
- Multi-strategy: Yes, native. Multiple algorithm compositions, portfolio construction models (Equal Weight, Mean-Variance, Black-Litterman), shared capital across strategies via PortfolioConstructionModel
- Language: C# core, Python/C# strategy API

What it does well:

- True multi-asset (equities, options, futures, forex, crypto)
- Pluggable fill models, slippage models, fee models
- Cloud paper trading with live data
- Walk-forward, live parity via shared event pipeline
- Recommend 2-4 weeks cloud paper trade before real capital, compare bar-by-bar P&L

What it does badly / cannot do:

- Platform lock-in to QuantConnect cloud for some features
- GitHub issue #4897: Only ConstantSlippageModel existed for years; VolumeShareSlippageModel and FixedSlippageModel were missing (added later)
- "Orders through some brokerages do not experience slippage in backtests, but in paper trading and live trading, orders may experience slippage" — documented divergence
- Complex configuration surface; steep learning curve
- C# core makes source-reading non-trivial for Python developers

Notable failure modes:

- Slippage model absence: Default zero-slippage in backtests vs. nonzero in paper/live → systematic over-optimism in backtests (issue #4897)
- QC forum "Error when going live in paper trading" (<https://www.quantconnect.com/forum/discussion/8475/>) — known category of paper-to-live transition errors

---

Freqtrade

- URL: <https://github.com/freqtrade/freqtrade>
- Category: Both (backtest + dry-run paper + live)
- Status: Actively maintained; large crypto community; heavily used in retail crypto algo trading
- Adoption: 30k+ GitHub stars; dominant retail crypto bot framework
- Time Model: OHLCV candle-driven event loop. Signals on bar CLOSE, entry at NEXT bar OPEN. No tick/order book.
- Multi-strategy: Yes (single instance, multiple strategies). Capital allocation per-strategy manual; no automatic shared-capital arbitration.
- Language: Python

What it does well:

- End-to-end crypto workflow: backtest → hyperopt → dry-run → live
- CCXT integration covers 100+ exchanges
- Trailing stop, stoploss-on-exchange, ROI tables
- FreqAI module for ML-based signals
- Lookahead analysis tool to detect future-data bugs

What it does badly / cannot do:

- Candle-only: no tick-level, no order book — microstructure blind
- Trailing stoploss backtesting WRONG for long timeframes: assumes price moves from open to high without decreasing — tight trails produce unrealistic results (<https://brookmiles.github.io/freqtrade-stuff/2021/04/12/backtesting-traps/>)
- Fill model assumes ALL limit orders fill — not true in live
- No partial fills modeled

Notable failure modes:

- GitHub issue #12662 (Dec 2024): Trades signal at specific times in backtest not received in dry-run — persistent timing divergence between modes
- GitHub issue #12675: Same class of bug — dry-run/backtest inconsistency in trade behavior
- Small-profit strategies: any strategy with <0.5% avg profit per trade typically loses live due to slippage + spread, even if profitable in backtest
- Capital rebalancing issue #8449: When running multiple strategies, capital must be manually realigned — no automatic pro-rata reallocation

---

Hummingbot

- URL: <https://github.com/hummingbot/hummingbot>
- Category: Both (paper + live; designed for market-making)
- Status: Actively maintained; foundation-governed
- Adoption: Large crypto market-maker community; DEX/CEX support
- Time Model: Event-driven, tick-level (live feed); paper mode uses real-time market data but simulated fills
- Multi-strategy: Yes, multiple strategies per instance; connector architecture separates exchange from strategy
- Language: Python

What it does well:

- Best-in-class for crypto market-making simulation
- Real-time paper mode using live order book feeds
- DEX support (Uniswap, etc.)

What it does badly / cannot do:

- Paper trade mode has recurring breakage issues:
  - Issue #7370 (Jan 2025): Paper trade mode broken on master branch
  - Issue #1762: Paper trading mode not executing orders
  - Issue #1672: Paper trading mode doesn't work in XEMM strategy
  - Issue #3510: KuCoin paper trade mode not working
- DEX paper mode problems: transaction signing, gas fees, failed transactions not modeled
- Double fill bugs on Hyperliquid perpetuals (issue #7294, #7295)
- "price_source_exchange" unknown error in paper mode (issue #4979)

Notable failure modes:

- Paper mode fill assumptions don't reflect DEX slippage, gas, failed txns
- Recurring connector-specific paper mode failures — paper mode is clearly not a first-class citizen in the codebase

---

Nautilus Trader

- URL: <https://github.com/nautechsystems/nautilus_trader> | <https://nautilustrader.io/>
- Category: Both (backtest + sandbox paper + live)
- Status: Actively maintained; Rust core since 2024 (replacing Cython); production-grade
- Adoption: Growing institutional and professional retail adoption; ~4k GitHub stars; strong reputation for correctness
- Time Model: Event-driven, nanosecond-resolution. Three environments: Backtest (historical + simulated venues), Sandbox (live data + simulated venues), Live. Shared NautilusKernel across all three → deterministic backtest-live parity
- Multi-strategy: Yes, native. Multiple strategies in one LiveNode, shared Cache + ExecutionEngine + Portfolio. True shared-capital architecture.
- Language: Rust core, Python control plane (PyO3 bindings)

What it does well:

- Best backtest-live consistency in the landscape (same kernel)
- Nanosecond-resolution event timestamps
- Order book reconstruction support (LOB-based fills)
- Pluggable fill models, fee models, latency simulation
- Actor-style strategy isolation prevents cross-strategy state bugs
- Rust core eliminates Python GIL bottlenecks in hot paths

What it does badly / cannot do:

- Fill model is still BASIC for complex scenarios: Issue #2194 (Jan 2025) — current FillModel handles only basic scenarios, lacks: competition for liquidity, varying liquidity across sessions, price impact for large orders, partial fills, queue position. Only `prob_fill_on_limit` and `prob_slippage` knobs.
- Enhancement proposal #2194 proposes `process_market_fills` and `process_limit_fills` hooks — not yet shipped
- Steep learning curve; heavy orchestration overhead
- Avoid concurrent `BacktestNode` executions in single process (singleton state conflict)
- Order flooding if inflight tracking not implemented
- Smaller community than Backtrader/Freqtrade; fewer tutorials

Notable failure modes:

- Issue #2194: Backtest fill model insufficient for realistic simulation — cannot model queue position, partial fills, or market impact. Live trading will diverge from backtest on any meaningful order size.
- "Live inputs and latency can still cause behavioral differences" — acknowledged in architecture docs

---

VectorBT / VectorBT PRO

- URL: <https://github.com/polakowo/vectorbt> | <https://vectorbt.pro/>
- Category: Backtest-only (open source); PRO adds some live data streaming
- Status: OSS version community-driven; PRO actively maintained (sponsorship/invite model)
- Adoption: 4k+ GitHub stars OSS; PRO has paid subscriber base
- Time Model: Vectorized (NumPy/pandas/Numba). Processes ALL bars simultaneously via array broadcasting — NOT event-driven. This is the fundamental design choice.
- Multi-strategy: Yes, native via array dimensions. Multiple strategies across assets in one call.
- Language: Python (Numba JIT for hot paths)

What it does well:

- Fastest backtesting for parameter sweeps (millions of simulations in seconds)
- Excellent for research-scale exploration
- PRO supports tick data and hybrid vectorized+event modes

What it does badly / cannot do:

- ZERO native live trading in OSS version — requires completely separate execution system
- Vectorized paradigm cannot model partial fills, order book state, latency, queue position
- Bar-close ambiguity problem applies identically to VBT
- Lookahead bias easy to introduce: vectorized operations on entire history simultaneously
- "No live trading capability" — confirmed by multiple sources
- Walk-forward requires workarounds (vectorized WF techniques exist but complex)

Notable failure modes:

- Structural lookahead: signal at bar T uses bar T data for execution also at bar T — discipline required to shift signals correctly
- GitHub issue #290: Questions about streaming/live data — answered "not designed for live" — fundamental OSS limitation

---

bt (Pmorgan bt)

- URL: <https://github.com/pmorissette/bt>
- Category: Backtest-only (portfolio weight/rebalance focused)
- Status: Mature/stable; niche maintained
- Adoption: Small but consistent academic/fixed-income user base
- Time Model: Portfolio weight + rebalance engine — NOT microstructure-driven. Works at allocation/weight level.
- Multi-strategy: Yes — designed for it. Multiple Algo compositions sharing capital.
- Language: Python

What it does well:

- Clean modular "Algo" composition for allocation strategies
- Ideal for Portfolio Balancer type strategies (target-weight rebalancing)
- Explicit rebalance mechanics

What it does badly / cannot do:

- No execution fidelity (no order mechanics, no fill model, no slippage)
- Ignores order types entirely
- Not suitable for signal-driven entry/exit strategies
- Fixed-income focused originally; crypto not considered

---

PyAlgoTrade

- URL: <https://github.com/gbeced/pyalgotrade>
- Category: Both (backtest + paper + live)
- Status: ABANDONED. Quickstart deprecated. Last meaningful commit years ago.
- Adoption: Was popular pre-2018; now superseded
- Time Model: Event-driven bar-level
- Multi-strategy: Limited
- Language: Python

What it does badly:

- Completely unmaintained; Python 2/3 transition era code
- Live trading adapters dead
- No crypto support

---

Jesse

- URL: <https://github.com/jesse-ai/jesse> | <https://jesse.trade/>
- Category: Both (backtest + live; paper mode available)
- Status: Actively maintained; crypto-focused
- Adoption: Growing crypto quant community; smaller than Freqtrade
- Time Model: Event-driven, "route" multi-pair configuration. Candle-level.
- Multi-strategy: Yes via route system; multiple pairs/strategies per instance
- Language: Python

What it does well:

- Crypto-native UX; cleaner API than Freqtrade for strategy writing
- 300+ indicators built-in
- Unified codebase across backtest/paper/live — reduced divergence risk
- Docker support; modern tooling

What it does badly / cannot do:

- Live trading is a PAID plugin — not fully open source end-to-end
- Smaller ecosystem/community vs Freqtrade
- Candle-only; no microstructure

---

Catalyst (crypto Zipline fork)

- URL: <https://github.com/scrtlabs/catalyst>
- Category: Both (backtest + live)
- Status: ABANDONED. "No longer actively supported." Last meaningful activity ~2019.
- Adoption: Was relevant 2017-2019 during Enigma/SCRT Labs era
- Time Model: Inherited Zipline's event-driven bar-close model
- Multi-strategy: Inherited Zipline limitations (single strategy)
- Language: Python

What it does badly:

- Dead. No support. Community forks not active.
- Inherited all Zipline fill-timing bugs plus crypto-specific exchange integration rot
- Python 2/3.6 era; installation failure on modern Python

---

hftbacktest

- URL: <https://github.com/nkaz001/hftbacktest>
- Category: Backtest-only (HFT/market-making focused)
- Status: Actively maintained; niche HFT tool
- Adoption: Small HFT research community; Python + Rust
- Time Model: Tick-level, full LOB reconstruction. Market-data replay. Nanosecond timestamps.
- Multi-strategy: Limited
- Language: Python/Rust

What it does well:

- Full tick-by-tick L2/L3 replay
- Queue position modeling (RiskAverseQueueModel, ProbQueueModel)
- Partial fill simulation (PartialFillExchange model)
- Best fill model realism for HFT/market-making among OSS tools

What it does badly / cannot do:

- CRITICAL assumption: "orders are small enough not to make any market impact" — large orders produce fictitious fills
- Queue models are approximations (no true L3 on most crypto exchanges)
- Market-data replay cannot alter simulated depth — orders consume a phantom book
- Steep setup: requires full LOB tick data (expensive/hard to obtain)

---

### Commercial / Semi-Open Platforms

---

Alpaca Paper Trading API

- URL: <https://alpaca.markets>
- Category: Paper trade (API-based, no backtest)
- Status: Active
- Time Model: Real-time event-driven (live data feed → simulated fills)
- Multi-strategy: Yes (via API; multiple bots can hit same account)

What it does badly / failure modes (from <https://hmmtrade.com/blog/alpaca-paper-gotchas>):

1. **Data feed divergence**: Free paper accounts get IEX feed only; live accounts get SIP (consolidated). IEX vs SIP gap = 5-50 bps on liquid names, much more on illiquid. Paper fills ≠ live fills — especially in the first seconds after bar close.
2. **403 errors**: `data.alpaca.markets/v2/stocks/bars` returns 403 on paper accounts — requires different endpoint
3. **Multi-symbol crypto bug**: Batch crypto bar requests return bars for only ONE symbol — must fetch individually
4. **Silent order rejection**: Invalid orders get HTTP 422, HTTP 200 + delayed rejection, OR silent drop — nondeterministic
5. **Account state lag**: Account metrics lag fills by ~1 second → "insufficient buying power" rejections on rapid consecutive orders
6. **Crypto vs stock asymmetry**: Paper crypto hits real Coinbase Prime orderbook; paper stocks use simulated fills — fundamentally different simulation quality in same "paper" account

---

Interactive Brokers Paper (Simulated) Trading

- URL: <https://www.interactivebrokers.com>
- Category: Paper trade (integrated in TWS/IBKR platform)
- Status: Active
- Time Model: Real-time, simulated fills from live market data

What it does badly / failure modes:

- Fills simulated from TOP of book — no deep book access
- Stop orders and complex order types ALWAYS simulated (behavior may differ from production)
- Partial fill bug (EliteTrader documented): "IB has a bug when there are partial fills — first fill price okay, but subsequent prices sometimes way off (like 3.5 points for ES futures)"
- Trade simulator rejects remainder of partially-executed exchange-directed market orders — may not match real exchange behavior
- Cannot model real-world liquidity/slippage: "trades may be filled differently in paper trading due to absence of real-world factors"

---

Binance Testnet

- URL: <https://testnet.binance.vision/>
- Category: Paper trade (testnet)
- Status: Active; FIX API added June 2024
- Time Model: Real-time (separate testnet exchange, own orderbook)

What it does badly:

- SEPARATE orderbook from mainnet — prices diverge from real Binance prices
- Liquidity is testnet-only, much thinner than real exchange
- Latency model differs from production
- Fill prices can differ significantly, especially during mainnet volatility events that don't propagate to testnet

---

Bybit Testnet / Demo Trading

- URL: <https://testnet.bybit.com> / demo.bybit.com
- Category: Paper trade (two modes: Testnet vs Demo)
- Status: Active
- Time Model: Demo uses real market prices; Testnet uses own simulated prices

What it does badly:

- Testnet has own orderbook — "may not always provide the most accurate representation of live trading conditions" (Bybit official docs)
- Not all APIs available in demo mode
- Demo account starts with simulated assets (50k USDT, 1 BTC, etc.) — no real market impact

---

Kraken Demo (Futures)

- URL: <https://demo-futures.kraken.com>
- Category: Paper trade (futures only)
- Status: Active
- Time Model: Mirrors live Kraken futures prices

What it does badly:

- Demo uses separate orderbook from live
- Liquidity thinner — fills unrealistic for large orders
- Spot Kraken has no official paper trading

---

TradingView Pine Script Backtesting

- URL: <https://www.tradingview.com>
- Category: Backtest-only (within TV platform)
- Status: Active; commercial
- Time Model: Bar-close based. Bar Magnifier feature added for intrabar fill simulation.
- Multi-strategy: Limited portfolio backtester via TV scripts

What it does badly / failure modes:

- Classic look-ahead bias: entry/exit on bar-close can use same bar's close — inflates results 15-30%
- Bar Magnifier (formerly checkbox, now "Bar detalization" menu) partially fixes intrabar ambiguity — but requires opt-in
- `process_orders_on_close=false` must be set manually to force next-bar-open execution
- No programmatic access to results — walled garden
- Survivorship bias in data by default

---

QuantConnect Cloud

- URL: <https://www.quantconnect.com>
- Category: Both (backtest + paper + live)
- Status: Active (commercial wrapper around LEAN)
- Multi-strategy: Yes

Failure modes same as LEAN above, plus:

- Cloud dependency for some data sources
- Pricing/subscription required for tick data and some assets

---

### Historical / Abandoned

---

Quantopian (2011-2020)

- URL: Dead. Zipline open-sourced; platform shutdown Oct 2020.
- Category: Both (backtest + paper + allocation to live fund)
- Status: DEAD

Why it died — the most instructive post-mortem:

1. **Cannot crowdsource alpha**: 200k+ users, millions of backtests, poor signal-to-noise. "You can't crowdsource alpha" — their own lesson
2. **Overfitting endemic**: Their own paper "All That Glitters Is Not Gold" (Wiecki et al., SSRN 2745220, 2016) showed: Sharpe ratio has R² < 0.025 for predicting out-of-sample performance. More backtests → bigger backtest-live gap (overfitting evidence)
3. **Slippage/liquidity ignored**: Novice strategies assumed zero slippage, unlimited liquidity → strategy illusions
4. **Adverse incentives**: Genuine quants kept real alpha proprietary; platform got low-quality strategies
5. **Live fund underperformance**: Capital returned to investors in 2020 after strategy underperformed

**Key lesson for hermes**: The backtest engine itself (Zipline) was not the cause of Quantopian's death — the WORKFLOW (crowdsourcing, no execution cost reality checks, Sharpe-based selection) was. But Zipline's fill timing bugs contributed to systematic overperformance in backtests.

---

Numerai

- URL: <https://numer.ai>
- Status: Active (different model — signal tournament, not execution simulator)
- Note: Not a backtest engine; mentioned for completeness as crowdsourced quant model. Uses ensemble ML on abstract features. Not relevant to hermes execution simulation needs.

---

### Academic / Research

---

ABIDES (Agent-Based Interactive Discrete Event Simulator)

- URL: <https://github.com/jpmorganchase/abides-jpmc-public> | <https://arxiv.org/abs/1904.12066>
- Category: Execution simulation / market simulation (academic)
- Status: Active research (JPMorgan); ABIDES-Economist extension (2024)
- Time Model: Discrete event, message-passing between agents. Latency models. NASDAQ ITCH/OUCH protocol-inspired.
- Multi-strategy: Yes — designed for multi-agent market simulation (tens of thousands of agents)
- Language: Python

What it does well:

- Most realistic market microstructure simulation: agent competition, LOB formation, latency modeling
- Configurable network latencies per agent
- Research-grade: used for RL market-making research

What it does badly / cannot do:

- Not designed for backtesting individual strategies — it's a market simulation environment
- ABIDES-Economist (2024) extends to macroeconomic agent learning via RL
- Requires calibration to be realistic — raw simulator may not match real market stylized facts without learning
- Not practical for day-to-day strategy backtesting

---

LOBSTER (LOB Reconstruction System)

- URL: <https://data.lobsterdata.com> | <https://ssrn.com/abstract=1977207>
- Category: Data + research tooling (NOT an execution simulator)
- Status: Active (commercial/academic); Humboldt University Berlin
- Time Model: N/A — provides L2 LOB data for NASDAQ stocks, not a simulator
- Multi-strategy: N/A

What it does well:

- High-quality L2 LOB data for all NASDAQ stocks from 2007-present
- Academic pricing (annual fee)
- Enables realistic LOB-based backtesting when combined with hftbacktest or NautilusTrader

What it does badly:

- Closed-source reconstruction logic — cannot inspect, adapt, or reproduce
- NASDAQ only — no crypto
- Cost is a barrier for small teams

---

## 2. SHORTLIST FOR SA-2 DEEP CODE READING

Ranked by: (a) architectural quality, (b) instructive design decisions, (c) documented failures to learn from.

1. NautilusTrader — HIGHEST PRIORITY

   - Why: Best architecture for hermes. Backtest/sandbox/live share one kernel. Rust core. Multi-strategy native. Issue #2194 reveals exactly what's missing from their fill model — our engine can solve these gaps. Read: `nautilus_trader/backtest/`, `nautilus_trader/execution/matching_engine.py`, `FillModel`.

1. QuantConnect LEAN

   - Why: Most mature multi-asset, multi-strategy portfolio construction. Best slippage model library. Read: `LEAN/Common/Orders/Slippage/`, `LEAN/Common/Orders/Fills/`, `LEAN/Common/Brokerages/DefaultBrokerageModel.cs`. Instructive on what a production-grade fill/fee/slippage pluggable system looks like.

1. Freqtrade

   - Why: Most battle-tested crypto bot. Dry-run mode architecture reveals the specific places where backtest→live translation breaks. Issues #12662 and #12675 are exact failure mode documentation. Read: `freqtrade/optimize/backtesting.py`, `freqtrade/exchange/`, dry-run vs live order routing.

1. hftbacktest

   - Why: Best fill model design for tick-level simulation (PartialFillExchange, RiskAverseQueueModel). Even though hermes won't be HFT, the queue model design is directly applicable to shadow-mode simulation. Read: `hftbacktest/backtest.py`, exchange models, queue position logic.

1. Zipline (quantopian original)

   - Why: Study as a FAILURE CASE. `VolumeShareSlippage.process_order`, `zipline/finance/slippage.py` shows exactly how fill timing bugs get embedded silently. The fill-at-next-bar-close bug is the canonical example of what NOT to do.

1. VectorBT PRO architecture (open source proxy)

   - Why: Best design for multi-strategy portfolio-level backtesting via array dimensions. Study how they implement capital accounting across multiple strategies simultaneously without an event loop. Instructive for Portfolio Balancer testing.

1. bt (pmorissette)

   - Why: Cleanest allocation/rebalance architecture. Directly applicable to Portfolio Balancer strategy. The Algo composition pattern is worth borrowing. Read: `bt/core.py`, `bt/algos.py`.

1. Backtrader (FAILURE CASE)

   - Why: Read as anti-pattern. Single-threaded Python event loop at scale. Cheat-on-Open workaround reveals the bar-close execution timing problem clearly. `backtrader/broker.py`, `backtrader/order.py`.

---

## 3. FAILURE INVENTORY FOR SA-4

### Category A: Fill Model / Price Timing Failures

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| Fill at next-bar CLOSE not OPEN | Zipline | github.com/quantopian/zipline/issues/2011 | VolumeShareSlippage uses next bar's CLOSE price for fills; should be OPEN. Creates ~12h look-ahead on daily bars. |
| Daily mode execution at market close +1 day | Zipline | github.com/quantopian/zipline/issues/2364 | Orders scheduled for "market open" execute at next day's close |
| Intrabar ambiguity (stop/TP same bar) | All OHLCV | SSRN 6240638 (Claeys, 2026) | 18.47% of NQ futures bars have both SL and TP within range; platforms must GUESS which hit first. Gap = 3,695 NQ points/1000 trades best vs worst assumption |
| Default zero-slippage in backtest | LEAN | github.com/QuantConnect/Lean/issues/4897 | Only ConstantSlippageModel existed for years; VolumeShare/Fixed missing → systematic backtest over-optimism |
| Paper uses simulated fills, live uses real | LEAN | QC docs | "Orders through some brokerages do not experience slippage in backtests, but in paper trading and live trading, orders may experience slippage" |
| Bar-close signal = fill at same bar | TradingView | TV Pine docs | Default `process_orders_on_close=true` — entry trigger and fill on same bar close = pure lookahead for any EOD strategy |
| Fill model too basic (no partial fills, no queue) | NautilusTrader | github.com/nautechsystems/nautilus_trader/issues/2194 | FillModel handles only `prob_fill_on_limit` + `prob_slippage`; no queue position, no liquidity competition, no partial fills |
| Market-data replay market impact | hftbacktest | docs.rs/hftbacktest/order_fill | "Small enough not to make market impact" assumption — large orders get fictitious fills |

### Category B: Latency Failures

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| IEX vs SIP data feed gap | Alpaca paper | hmmtrade.com/blog/alpaca-paper-gotchas | Paper gets IEX, live gets SIP → 5-50 bps divergence on liquid names, worse on illiquid. Worst in first seconds after bar close. |
| Account state lag | Alpaca paper | hmmtrade.com/blog/alpaca-paper-gotchas | Account metrics lag fills by ~1 second → false "insufficient buying power" on rapid orders |
| Testnet/mainnet price divergence | Binance/Bybit testnet | Official docs + community reports | Separate orderbooks → prices diverge, especially during mainnet volatility events |
| No latency modeling | All OHLCV frameworks | Literature review | Bar-close frameworks assume zero latency from signal to execution — impossible in live trading |

### Category C: Data Quality Failures

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| Adjusted vs unadjusted prices | Zipline | github.com/quantopian/zipline/issues/490 | Quantopian cloud used unadjusted; local Zipline used adjusted → strategy divergence between platforms |
| Survivorship bias | Most OHLCV engines | Literature | Default: only active assets included → backtests exclude delisted/bankrupt assets → overstated Sharpe |
| Crypto multi-symbol batch bug | Alpaca | hmmtrade.com/blog/alpaca-paper-gotchas | Multi-symbol crypto bar requests return bars for only one symbol |
| Historical data ≠ live data timing | Freqtrade | github.com/freqtrade/freqtrade/issues/12662 | Same signal at 12:24 in backtest doesn't trigger in dry-run — different data ingestion timing |

### Category D: Partial Fill Handling

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| All limit orders assume full fill | Freqtrade | brookmiles.github.io/freqtrade-stuff/backtesting-traps | Backtest assumes all orders fill; dry-run/live has unfilled limits especially with low volume |
| Partial fill bug | IBKR Paper | NinjaTrader forum + EliteTrader | First partial fill price correct, subsequent prices "way off" (3.5 pts on ES) |
| No partial fill model | Most bar-close engines | Literature | Standard OHLCV engines assume fill-or-no-fill; real exchanges frequently partially fill large orders |

### Category E: Cross-Strategy Capital Bugs

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| Manual capital rebalancing | Freqtrade multi-bot | github.com/freqtrade/freqtrade/issues/8449 | Multiple Freqtrade instances can't auto-rebalance capital — must set manually per strategy |
| Implementation risk across engines | Multiple engines | arxiv.org/abs/2603.20319 (Yin et al., 2026) | Same strategy through 5 different engines → materially different reported performance. Transaction cost implementation is THE source of divergence (at zero cost all engines agree exactly). |

### Category F: Backtest Platform / Workflow Failures

| Failure | System | Source | Description |
| --------- | -------- | --------- | ------------- |
| Quantopian crowdsourcing alpha failure | Quantopian | SSRN 2745220; multiple post-mortems | Sharpe R² < 0.025 for predicting live performance. More backtesting → more overfitting. Platform died Oct 2020. |
| Trailing stop exploitation | Freqtrade | brookmiles.github.io/freqtrade-stuff/backtesting-traps | Very tight trailing SL produces unrealistic results — assumes price moves open→high without dipping first |
| Paper mode as afterthought | Hummingbot | Multiple GitHub issues (#7370, #1762, #1672, #3510, #4979) | Paper mode broken repeatedly; not first-class. Issues span 2020-2025. |
| Silent order rejection nondeterminism | Alpaca | hmmtrade.com/blog/alpaca-paper-gotchas | Invalid orders get HTTP 422, HTTP 200 + delayed reject, or silent drop — impossible to build reliable wrapper |

---

## 4. TIME MODEL LANDSCAPE SUMMARY

### Distribution

| Time Model | Systems | Count |
| ------------ | --------- | ------- |
| Bar-close / OHLCV event-driven | Zipline, Backtrader, Freqtrade, Jesse, Catalyst, PyAlgoTrade, TradingView | 7 |
| Vectorized (all-at-once) | VectorBT (OSS + PRO) | 1 (+1 PRO hybrid) |
| True event-driven (tick/quote/trade stream) | NautilusTrader, LEAN, hftbacktest, ABIDES | 4 |
| Allocation/rebalance only (no microstructure) | bt, pysystemtrade | 2 |
| Paper/live only (no backtest) | Alpaca, IBKR paper, exchange testnets | ~6 |

### Why Bar-Close Won for Most Tools

- Data availability: OHLCV is cheap, universal, and sufficient for daily signals
- Simplicity: no need to model order book or queue position
- Speed: vectorized OHLCV is 100-1000x faster than tick replay

### Problems Bar-Close Caused

1. **Intrabar ambiguity**: When SL and TP both fall within a single bar's range, the engine must guess which was hit first. Quantified by Claeys (SSRN 6240638, 2026): 18.47% of NQ bars ambiguous; worst-case vs best-case = 3,695 NQ points/1000 trades.
2. **Fill timing**: Signal at bar T, fill at bar T+1 open is correct. Fill at bar T close (Zipline's VolumeShareSlippage bug) or bar T+1 close (default Zipline daily) introduces lookahead.
3. **Spread/microstructure blindness**: No bid/ask; fills assume mid-price or close price with no spread cost.
4. **Unrealistic limit order fills**: Bar-close cannot model whether a limit order's price was actually touched and what queue position it had.

### Why Tick-Level Is Expensive But Superior

- Requires L2 LOB data (expensive: LOBSTER for NASDAQ; exchange WebSocket for crypto)
- Realistic for any order type, partial fills, queue position
- NautilusTrader and hftbacktest both support tick-level; hftbacktest most complete
- Hermes recommendation: **tick/trade stream for shadow-mode** (live orderbook available free from Binance/Kraken WebSocket); **1m OHLCV as minimum for backtesting** with correct next-open fill semantics

---

## 5. MULTI-STRATEGY LANDSCAPE SUMMARY

### Support Matrix

| System | Multi-strategy? | Capital Model | Pattern |
| -------- | ---------------- | --------------- | --------- |
| NautilusTrader | Yes, native | Shared via Cache + Portfolio | Multiple strategies in one LiveNode; unified ExecutionEngine |
| LEAN | Yes, native | Portfolio construction models (Equal Weight, MV, BL) | PortfolioConstructionModel layer; AlphaModel outputs combined |
| Freqtrade | Yes (same instance) | Manual allocation; no auto-rebalance across strategies | Pair-strategy routing; capital_per_trade per strategy |
| Backtrader | Yes (degraded) | Shared broker; strategies compete | Cerebro.addstrategy() × N; slow at scale |
| VectorBT | Yes (array dims) | Vectorized across assets/strategies simultaneously | Array broadcasting; not true portfolio simulation |
| bt | Yes (design center) | Allocation tree; Algo composition | Rebalance-based; explicit weight targets |
| Jesse | Yes (route system) | Per-route capital | Route config specifies capital per pair/strategy |
| hftbacktest | Limited | Single strategy focus | No native multi-strategy |
| Zipline | No | Single strategy | Pipeline for universe, not multi-strategy |
| PyAlgoTrade | Limited | Single strategy focus | — |

### Patterns Observed

**1. Allocator pattern (LEAN):** Central allocator receives alpha signals from N strategies, constructs target weights, sends unified orders. Strategies don't know about each other's capital. Clean separation: alpha generation → weight construction → execution.

**2. Sub-portfolio pattern (NautilusTrader):** Multiple strategy actors share a single portfolio object. Each strategy sees the full portfolio state. Risk manager operates on the combined position. No explicit weight-based allocation — strategies compete for execution.

**3. Capital-per-strategy pattern (Freqtrade/Jesse):** Each strategy gets a fixed capital slice. No cross-strategy rebalancing. Simplest but inflexible — capital gets stranded in one strategy while another is starved.

**4. Manual/external pattern (multi-bot setups):** Multiple independent bot instances, each with its own capital. External reconciliation required. Freqtrade issue #8449 shows the pain: capital rebalancing is purely manual.

### Recommendation for Hermes

- Portfolio Balancer + Swing Strategy sharing capital → **Sub-portfolio pattern** (NautilusTrader model) is most natural
- Need: unified position manager that both strategies query before placing orders; risk manager that enforces total capital limits
- Critical: Swing strategy is signal-driven (async entry/exit); Portfolio Balancer is periodic-rebalance. These have fundamentally different order lifecycles and need explicit arbitration if capital is truly shared.

---

## 6. CONFIDENCE ASSESSMENT

| Category | Confidence | Notes |
| ---------- | ------------ | ------- |
| Inventory — OSS systems (status, time model, adoption) | HIGH | Multiple primary sources; GitHub confirmed |
| Inventory — Commercial paper trading limitations | HIGH | Primary sources (official docs, direct blog with specifics) |
| Failure evidence — Fill model bugs | HIGH | GitHub issues linked with specific issue numbers and descriptions |
| Failure evidence — Quantopian post-mortem | HIGH | Multiple independent sources + their own published research paper (SSRN 2745220) |
| Failure evidence — Exchange testnet divergence | MEDIUM | Official docs confirm different orderbooks; specific bps divergence not formally measured |
| Failure evidence — Hummingbot paper mode | HIGH | Multiple GitHub issue numbers with dates |
| Shortlist justification | HIGH | Based on architecture quality assessment from multiple 2025-2026 comparative sources |
| Time model landscape counts | HIGH | Directly verified per-system |
| Multi-strategy support matrix | MEDIUM-HIGH | NautilusTrader and LEAN verified from docs; others from secondary sources |
| Academic citations (implementation risk, SSRN papers) | HIGH | arxiv + SSRN links verified |

---

## 7. KEY SOURCES

- Freqtrade backtesting traps: <https://brookmiles.github.io/freqtrade-stuff/2021/04/12/backtesting-traps/>
- Zipline fill timing issue: <https://github.com/quantopian/zipline/issues/2011>
- Zipline execution timing: <https://github.com/quantopian/zipline/issues/2364>
- Zipline price adjustment issue: <https://github.com/quantopian/zipline/issues/490>
- LEAN slippage model missing: <https://github.com/QuantConnect/Lean/issues/4897>
- NautilusTrader fill model limitations: <https://github.com/nautechsystems/nautilus_trader/issues/2194>
- Freqtrade dry-run divergence #12662: <https://github.com/freqtrade/freqtrade/issues/12662>
- Freqtrade dry-run divergence #12675: <https://github.com/freqtrade/freqtrade/issues/12675>
- Freqtrade capital rebalancing: <https://github.com/freqtrade/freqtrade/issues/8449>
- Hummingbot paper mode broken: <https://github.com/hummingbot/hummingbot/issues/7370>
- Hummingbot paper mode no execution: <https://github.com/hummingbot/hummingbot/issues/1762>
- Alpaca paper gotchas: <https://hmmtrade.com/blog/alpaca-paper-gotchas>
- Implementation risk in backtesting (Yin et al., 2026): <https://arxiv.org/abs/2603.20319>
- Intrabar ambiguity (Claeys, 2026): <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6240638>
- Quantopian "All That Glitters" (Wiecki et al., 2016): <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220>
- ABIDES (JPMorgan, 2019): <https://arxiv.org/abs/1904.12066>
- LOBSTER: <https://data.lobsterdata.com>
- Python backtesting landscape 2026: <https://python.financial/>
- hftbacktest fill model: <https://hftbacktest.readthedocs.io/en/latest/order_fill.html>
- NautilusTrader architecture: <https://nautilustrader.io/docs/latest/concepts/architecture/>
- NautilusTrader backtesting best practices: <https://deepwiki.com/nautechsystems/nautilus_trader/6.6-backtesting-best-practices>
