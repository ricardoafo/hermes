# SA-4 Failure Post-Mortems — Deep + Broad Survey

**Date**: 2026-08-24
**Author**: SA-4 sub-agent
**Engram topic key**: `research/paper-trading-sa4-post-mortems`
**Status**: FINAL
**Depends on**: SA-0 landscape scouting (`research/paper-trading-sa0-landscape`)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Knight Capital Deep-Dive](#2-knight-capital-deep-dive)
3. [Retail Crypto Wipeout Patterns](#3-retail-crypto-wipeout-patterns)
4. [HFT Firm Engineering Lessons](#4-hft-firm-engineering-lessons)
5. [Regulatory / Systemic Failures](#5-regulatory--systemic-failures)
6. [Academic Case Studies](#6-academic-case-studies)
7. [Operational Failures Inventory](#7-operational-failures-inventory)
8. [Hermes Defense Matrix](#8-hermes-defense-matrix)
9. [Confidence Assessment](#9-confidence-assessment)
10. [Key Citations](#10-key-citations)

---

## 1. Executive Summary

SA-0 mapped the landscape of backtest/paper-trading engine failures. SA-4 goes deeper on three categories SA-0 could not fully address from landscape-level scanning:

**The irreducible truth**: every failure in this survey reduces to the same root cause — a system that believed its own model of the world instead of querying the actual world. Knight Capital believed eight servers were identical; they weren't. Retail crypto bots believed backtests represented liquidity; they didn't. 3Commas trusted API keys were secrets; they weren't. FTX bots trusted the exchange would remain solvent; it didn't.

**Five failure classes that matter most for hermes, ranked by likelihood × severity:**

1. **State drift on reconnect** (HIGH likelihood, HIGH severity): WebSocket close-1006 is the most common silent killer in production crypto bots. When the connection drops and the bot reconnects without sequence-number gap detection, local position state diverges from exchange reality. The bot places orders as if positions it no longer holds are active — or fails to exit positions it doesn't know it holds. Paper trading catches this only if it runs on the same live WebSocket infrastructure as live; if paper uses REST polling it will never surface this failure class.

2. **Fill model illusion** (HIGH likelihood, MEDIUM severity): Paper trading systems — including Cryptohopper's own documented divergence — assume unlimited liquidity and always-fill semantics. Cryptohopper explicitly warns "an order successfully executed in paper trading may not be filled when using real funds." Swing strategies are less exposed than HFT but are not immune: a limit entry that fills in paper mode may sit unfilled in live for hours, then fill at a worse price during volatility.

3. **Clock drift breaking auth** (MEDIUM likelihood, HIGH severity): Clock drift of ≥5 seconds causes exchange-level signature rejection (`timestamp_out_of_range`). The failure appears as auth errors, not as a trading bug — the bot's orders never reach the exchange. The deceptive feature: public market data continues to arrive normally, so the bot appears healthy while firing zero orders. A paper-trading system that uses REST polling with no timestamp validation will never surface this.

4. **Exchange death spiral** (LOW likelihood, CATASTROPHIC severity): FTX proved that an exchange can become insolvent while still accepting orders. Bots that ran on FTX during the Nov 8–12, 2022 collapse continued executing trades against a book that would never settle. Paper trading on any exchange provides zero protection against exchange insolvency — the paper ledger is only as trustworthy as the exchange itself.

5. **Implementation divergence across cost regimes** (HIGH likelihood, MEDIUM severity): Yin et al. (2026) found that at zero transaction cost, all five backtesting engines produce identical equity curves. Divergence scales monotonically with cost intensity (Spearman ρ=0.93). Backtrader's `percabs` parameter divided commissions by 100 — a 100× undercharge that inflates every backtest result. Hermes must treat its own fee model as a first-class testable artifact.

**Hermes-specific verdict**: The paper-trading layer must (a) share WebSocket infrastructure with live mode, (b) implement sequence-number gap detection on reconnect, (c) validate clock offset against Binance serverTime at startup and on reconnect, (d) model fills as probabilistic events not guaranteed events, and (e) treat the cost model as a tested component with its own unit tests against known trade scenarios. None of these require a market microstructure PhD — they require engineering discipline.

---

## 2. Knight Capital Deep-Dive

### 2.1 Full Technical Timeline

| Time (EDT, Aug 1 2012) | Event |
| --- | --- |
| Jul 27–31, 2012 | Deployment of new SMARS binary via script that silently failed SSH connection to 1 of 10 servers. No automated verification. No second-engineer sign-off required. One server left running outdated binary. |
| 9:30:00 AM | NYSE market opens. Retail Liquidity Program (RLP) begins receiving orders. |
| 9:30:01 AM | 9 correctly-deployed servers process RLP orders as designed. The 1 outdated server interprets the RLP activation flag as the legacy "Power Peg" command — an 8-year-old dormant code path. |
| 9:30–10:15 AM | Power Peg continuously regenerates child orders in response to each incoming parent order without acknowledging fills. It accumulates positions "not allocated to any trading strategy." Trading algorithms have no visibility into these positions because position reporting for Power Peg had been broken during a 2005 refactor and never restored. |
| ~9:45 AM | Automated alerts fire, identifying issues with the system. Alerts not acted upon — no escalation protocol exists that would halt trading. |
| ~10:00 AM | Knight operations staff, attempting to fix the problem, unknowingly roll back to the same buggy binary. Losses accelerate. |
| 10:15 AM | Trading operations manually halted. 45 minutes elapsed. |
| EOD | Loss: $460.1M. 212 parent orders → 4,000,000+ child executions → 397M shares across 154 stocks → $7.65B in gross exposure. |

### 2.2 Root Cause Classification

| Axis | Classification |
| --- | --- |
| Primary | **Operational**: incomplete deployment, no verification, no rollback validation |
| Secondary | **Monitoring gap**: alert fired, ignored; no per-server health check; no unallocated-position circuit breaker |
| Tertiary | **Code hygiene**: flag reuse (RLP bit = Power Peg bit on old server); dead code not removed |

The SEC's administrative proceeding (Release 34-70694, Oct 16 2013) identified three control failures:

1. Knight did not have adequate controls to monitor the output of its system
2. Knight did not have sufficient safeguards to prevent entry of erroneous orders
3. Knight did not have procedures in place to halt trading in response to aberrant activity

Knight paid $12M to settle. The firm was acquired by Getco LLC (now Virtu Financial) shortly after.

### 2.3 What Monitoring Failed

- **No post-deployment server-uniformity check**: Each server should have been queried post-deploy to confirm it was running the expected binary hash. Zero-cost to implement, never existed.
- **Alert-to-halt pathway absent**: The automated pre-market alert fired. There was no automated circuit breaker that would pause order routing when the alert condition was met. Humans saw the alert and lacked authority or procedure to halt.
- **No gross position exposure limit per strategy-slot**: Power Peg accumulated $7.65B in gross exposure with no position limit. A simple "if gross position > $X from unallocated source, halt" would have stopped it in the first second.
- **Position reporting broken since 2005 and unknown**: The 2005 refactor broke Power Peg's position reporting. No one knew. This is the most damning detail — a safety system had been silently broken for 7 years.

### 2.4 What Shadow-Mode / Paper-Trading Would Have Caught

A proper shadow-mode test on August 1, 2012 would have revealed the anomaly within the first second:

- **Shadow position divergence**: In shadow mode, the "live" system (9 correct servers) accumulates position X. The shadow ledger (reflecting all 10 servers including the faulty one) accumulates position X + delta. The delta corresponds to the Power Peg accumulation. If the shadow system maintained per-server position attribution, the diverging server would be immediately visible.
- **Unallocated position alert**: Shadow mode with a simple invariant — "every open position must map to exactly one active strategy" — would have fired on the first Power Peg child order, which belonged to no strategy.
- **Rate-of-order-generation alarm**: The Power Peg server was generating child orders at an anomalous rate relative to the 9 normal servers. A per-server order rate monitor would have detected the outlier within 100ms of market open.

**Critical lesson for hermes**: Even if hermes never reaches Knight Capital's scale, the architectural principle applies: every open position must be traceable to an originating strategy and order. "Orphan position" is always an alert condition. Shadow mode must attribute positions to strategies, not just track aggregate P&L.

### 2.5 Sources

- SEC Release 34-70694 (Oct 16, 2013): <https://www.headlandstech.jp/static/file/34-70694.pdf>
- Speculative Branches technical post-mortem: <https://specbranch.com/posts/knight-capital/>
- Henrico Dolfing case study: <https://www.henricodolfing.ch/en/case-study-4-the-440-million-software-error-at-knight-capital/>
- Postmortems.app: <https://postmortems.app/postmortem/ef3cc3e0-c51c-4f5d-8dd0-cc8a8dfdcbb2>
- WilmerHale settlement note: <https://www.wilmerhale.com/en/insights/client-alerts/knight-capital-settles-rule-15c3-5-violations-with-sec-agrees-to-pay-12-million>

---

## 3. Retail Crypto Wipeout Patterns

### 3.1 The 3Commas API Breach (December 2022)

**What happened**: In December 2022, an attacker accessed the 3Commas database and exfiltrated ~100,000 API keys. Using the stolen keys, attackers executed trades on behalf of victims: they bought low-cap coins on victim accounts and dumped them from their own wallets — a classic pump-and-dump where victim accounts provided the buy-side liquidity.

**Financial impact**: ~$20M total, with on-chain investigator ZachXBT verifying $14.8M in losses across 44 identified victims.

**Mechanism of attack**: The API keys permitted trading but not withdrawal. Attackers exploited the gap between "can trade" and "can withdraw" — they manufactured losses on victim accounts by forcing bad trades, then profited on the other side.

**Paper trading relevance**: 3Commas offers paper trading on its free tier. Paper trading requires no exchange API connection at all. This means:

- Paper trading users were completely unaffected by the breach (no API keys to steal)
- The breach was only possible because live-trading users had connected API keys stored on 3Commas servers
- Paper trading gave zero advance warning of the API key storage vulnerability — a systemic architectural risk invisible in simulation

**Hermes lesson**: Any paper-trading system that stores or caches API credentials server-side inherits this attack surface. Hermes must never transmit testnet API keys to a third-party intermediary. The paper trading layer should use a separate credential set (Binance testnet keys) that has zero financial exposure even if leaked.

**Sources**:

- Halborn analysis: <https://www.halborn.com/blog/post/explained-the-3commas-breach-december-2022>
- 3Commas official disclosure: <https://3commas.io/blog/notice-on-api-data-disclosure-incident>
- Bitsgap API safety guide: <https://bitsgap.com/blog/is-it-safe-to-connect-your-exchange-api-to-a-trading-bot>

### 3.2 LUNA/UST Collapse — Grid Bot Wipeout (May 2022)

**What happened**: LUNA traded at $119 on April 5, 2022. By May 13, it was at $0.0003 — a 99.99% collapse over 38 days, with the sharpest 99% drop occurring in 4 days (May 9–13). UST lost its $1 peg on May 7 due to a coordinated $500M UST sell-off across Curve and Binance pools, overwhelming the algorithmic stabilization mechanism.

**Grid bot behavior**: Grid trading bots are configured to buy on dips and sell on recoveries within a defined price range. As LUNA fell through each grid level, bots automatically purchased more LUNA — perfectly executing their designed strategy. The strategy was correct given the assumption "price will recover within the grid range." That assumption failed catastrophically when the asset went to near zero.

**Reported losses**: Grid bot users who had automated DCA strategies on LUNA reported losses of the full deployed capital. The "emergency brake" on LUNA:USDT grid strategies triggered around $75 for those who had set one, but many users had no stop-loss configured.

**Paper trading failure mode**: A user running a LUNA grid bot in paper trading in April 2022 would have seen excellent results — LUNA had high volatility and grid strategies profit from oscillation. The paper trading environment gave no warning of impending structural collapse. This is the **simulation fidelity / regime change problem** in its most extreme form.

**Root cause classification**: Market-extreme + capital model failure. The grid bot's capital model assumed symmetric risk. Crypto assets can go to zero; the model did not account for this terminal condition.

**Hermes lesson**: Swing strategies on Binance testnet face an inverse version of this problem — testnet prices can diverge from mainnet, so a swing strategy that looks profitable on testnet may be using prices that were never real. Hermes must track both testnet P&L and what the same trades would have yielded at mainnet prices (shadow-of-shadow mode).

**Sources**:

- Harvard Law LUNA anatomy: <https://corpgov.law.harvard.edu/2023/05/22/anatomy-of-a-run-the-terra-luna-crash/>
- MIT Sloan CFI analysis: <https://mitsloan.mit.edu/cfi/anatomy-a-run-terra-luna-crash>
- CoinDesk LUNA post-mortem: <https://www.coindesk.com/markets/2022/05/15/the-collapse-of-ust-and-luna-was-devastating-but-there-is-still-hope-for-crypto>

### 3.3 TradingView Pine Script → Live Trading Disasters

**The canonical failure pattern**: A TradingView strategy backtest shows 200% annual returns. The trader connects it to an execution platform. Live performance is flat or negative within weeks.

**Root cause — `process_orders_on_close`**: TradingView Pine Script's default setting executes orders on bar close at the close price of the same bar that generated the signal. This is lookahead bias. The correct setting, `process_orders_on_close=false`, delays execution to the open of the next bar — which is the only physically realizable fill. The difference between these two modes produces inflated backtests of 15–30% for daily strategies; for strategies with tight SL/TP, the inflation can exceed 100%.

**The `calc_on_order_fills` trap**: Enabling `calc_on_order_fills=true` grants the script access to confirmed OHLC values of the current historical bar at the moment of fill — information that would be unavailable in real time. This is a subtler form of lookahead that TradingView's own documentation flags as "might lead to unrealistic results on historical bars."

**The `request.security` lookahead trap**: Using `lookahead=barmerge.lookahead_on` in multi-timeframe scripts gives the script the completed value of a higher-timeframe bar before it closes. A script running on a 1-minute chart with a daily EMA computed this way "knows" the daily EMA at 9:31 AM before the daily bar closes at 4:00 PM.

**Hermes lesson**: Pine Script is not hermes, but the bug class is universal. Any bar-processing code in hermes that uses bar-close data to compute signals must enforce that fills use next-bar-open prices. The test suite must include a "lookahead detector" scenario: a test where bar T close = signal trigger and bar T+1 open ≠ bar T close — the fill must land at T+1 open, not T close.

**Sources**:

- Medium (Willow the Trader): <https://medium.com/@techacademies/why-your-perfect-tradingview-strategy-fails-in-live-trading-30a875226d6b>
- TradingView support: <https://es.tradingview.com/support/solutions/43000614705/>
- TradingView Pine Script FAQ: <https://www.tradingview.com/pine-script-docs/faq/strategies/>

### 3.4 Freqtrade Backtest-to-Dry-Run Divergence (Documented Community Pattern)

SA-0 cited issues #12662 and #12675. SA-4 extends with a broader pattern from the community:

**Issue #8518 (2023)**: User reported: backtest shows -8.97% for Jan–Apr 2023; live trading shows monthly losses of -3.70% to -13.42% with only one positive month. Root cause investigation: execution timing delays — "actual entries and exits happen slightly late than they should." No firm resolution reached.

**Issue #7502 (2022)**: "Not getting the same results between backtest and dryrun." Root cause: informative multi-timeframe pairs have different data availability in real-time vs. backtesting — candle alignment differs because live data arrives incrementally while backtesting uses pre-downloaded closed bars.

**Issue #8451 (2022)**: "The difference between backtest and dry_run results." Freqtrade's official documentation acknowledges: "Entry rates in dry runs might not match backtesting, which means profit calculations will also differ. It is normal if ROI, stoploss, trailing stoploss and callback exits are not identical."

**Issue #12894 (2024)**: Signals differ between backtest and dry-run for the same time period on 5m/30m strategies. Suspected cause: multi-timeframe informative pairs and candle alignment at real-time ingestion boundaries.

**The systemic pattern**: Backtesting uses downloaded, completed OHLCV bars. Dry-run and live use the exchange's current candle, which is still open. The signal computed on a closed bar (backtest) often differs from the signal computed at the current moment on an open bar (live) — even if you specify the same time range.

**Hermes lesson**: If hermes uses any multi-timeframe signal (e.g., 1h trend filter on a 15m entry strategy), the signal computation code must be parameterized by "closed bar only" vs. "current bar included" and must be tested in both modes to ensure they converge on bar close.

**Sources**:

- Freqtrade #8518: <https://github.com/freqtrade/freqtrade/issues/8518>
- Freqtrade #7502: <https://github.com/freqtrade/freqtrade/issues/7502>
- Freqtrade #12894: <https://github.com/freqtrade/freqtrade/issues/12894>
- Freqtrade #8451: <https://github.com/freqtrade/freqtrade/issues/8451>

### 3.5 Cryptohopper — Documented Paper-vs-Live Divergences

Cryptohopper officially documents the following divergences between paper trading and live trading:

| Dimension | Paper Trading | Live Trading |
| --- | --- | --- |
| TA scan frequency | Every 10 minutes | 2 min (Hero), 5 min (Adventurer), 10 min (Explorer) |
| Liquidity assumption | Unlimited (always fills) | Real exchange supply/demand |
| API connection | None required | Mandatory (exchange API keys) |
| Fill guarantee | Always fills | "May not be filled when using real funds" |

The TA scan frequency difference is particularly damaging for swing strategies: a signal that appears on the Hero tier's 2-minute scan might not appear for 10 minutes in paper mode, producing different entry/exit times and therefore different P&L.

**Hermes lesson**: If hermes paper trading uses a different polling/WebSocket cadence than live mode, the timing divergence will compound with every signal. Paper mode must run on the same event loop and same cadence as live mode. There must be no "paper mode shortcut" that batches or throttles data ingestion.

**Sources**:

- Cryptohopper official doc: <https://support.cryptohopper.com/en/articles/9173649-are-there-differences-between-trading-with-paper-trading-and-real-funds>

### 3.6 Retail Slippage Shock — The $9M Dogwifhat Example

A documented case from January 2024: a $9 million market order on a low-liquidity altcoin (dogwifhat/WIF) produced over $5.7 million in slippage — a 63% execution cost. The backtest modeled a fixed-percentage slippage of ~0.3%; the actual order swept through the entire order book.

**Root cause classification**: Fill model + capital scale mismatch. The backtest slippage model was calibrated on small order sizes. The live trade was orders of magnitude larger.

**Pattern in retail community**: Multiple accounts confirm the failure mode: strategies that show backtest Sharpe > 2 with <0.5% average profit per trade fail live because:

1. Slippage eats the edge entirely (especially altcoins, especially market orders)
2. Spread cost was modeled as zero or constant but is actually variable and regime-dependent
3. Exchange spread on BTC/USDT is 0.1–0.3 bps in normal conditions; during volatility it spikes to 2–4 bps — a 10–40× increase

**Hermes lesson**: The swing strategy on Binance operates on BTC/USDT or similar tier-1 pairs where spread is well-behaved. But any extension to altcoins must model spread as a function of volatility regime, not a constant. The fill simulator must sample from a spread distribution, not a single number.

**Sources**:

- Paybis backtest guide: <https://paybis.com/blog/how-to-backtest-crypto-bot/>
- Coin Bureau bot mistakes: <https://coinbureau.com/guides/crypto-trading-bot-mistakes-to-avoid>
- Medium (bot losses): <https://medium.com/@nancycampbell896azhelenwlidxh/i-tested-23-trading-bots-and-lost-14-000-heres-what-actually-works-a4bc81f2c0af>

---

## 4. HFT Firm Engineering Lessons

Note: HFT firms publish engineering content selectively. The lessons below are extracted from public-domain sources: tech blogs, podcast transcripts, conference talks, and job descriptions. These firms do not publish trading strategy details.

### 4.1 Jane Street — Signals and Threads

Jane Street's "Signals and Threads" podcast (24 episodes as of June 2026) is the most accessible source of production trading system engineering principles. Key episodes and lessons:

**Episode 6: Clock Synchronization (Oct 2020)** — Guest: Chris Perl
The episode specifically addresses achieving microsecond-level clock accuracy across networks at scale. This is not academic curiosity: in trading systems, a timestamp that is 5ms off can cause order rejection or, worse, a phantom fill that the exchange accepted but the bot doesn't credit. Jane Street operates at a scale where clock drift must be detected and corrected continuously, not just at startup.

*Hermes implication*: Clock offset validation must be a startup gate, not a one-time check. Hermes must re-validate clock offset on every WebSocket reconnect and on a periodic background schedule.

**Episode 3: Multicast and the Markets (Sep 2020)** — Guest: Brian Nigito
Covers electronic exchange architecture: single-threaded order processing, multicast for market data, and the design that makes exchanges deterministic. Key insight: exchanges are designed around the assumption that all participants share the same view of time and price. Any bot that has a stale or desynchronized view of the order book is operating on a different reality than the exchange.

*Hermes implication*: The paper trading fill model must be fed from the same live order book snapshot that the signal engine sees. A paper fill model that uses a 30-second-stale price is as wrong as the bot that uses a stale clock.

**Episode 18: Performance Engineering on Hard Mode (Nov 2023)** — Guest: Andrew Hunter
"Optimizing multithreaded systems; profiling techniques for trading systems with bursty, low-latency workloads." Key quote paraphrased: finding performance wins in trading systems — which operate at smaller scale than hyperscalers but have bursty, low-latency workloads — is often trickier than optimizing at hyperscale.

*Hermes implication*: The swing strategy is NOT HFT. Multi-minute holds mean latency is not the constraint. However, the lesson about burstiness applies: market open and close generate order bursts that can overwhelm rate limits exactly when accurate execution matters most.

**Episode 20: Solving Puzzles in Production (Oct 2024)** — Guest: Liora Friedberg
Jane Street trains Production Engineers via "tabletop simulation and hands-on exercises" — essentially paper-trading for their infrastructure. The lesson: high-stakes systems should be rehearsed in simulation before they encounter real conditions.

*Hermes implication*: The paper trading layer is not just a feature; it is the infrastructure rehearsal system. It must be run long enough and on enough market conditions to be statistically meaningful.

**Sources**:

- Signals and Threads: <https://signalsandthreads.com/>
- Episode index: <https://blog.janestreet.com/announcing-signals-and-threads-index/>

### 4.2 Optiver — Three Pillars of Trading

Optiver's public tech blog post "Engineering the Three Pillars of Trading: Pricing, Risk, and Execution" identifies the critical failure mode for execution systems:

> "If risk calculations and price publishing lag behind market changes, we'll miss out — because prices change rapidly, and so does the risk."

Optiver's systems are designed to "scale with the size of our trading operation and not the busyness of the market" — meaning performance degrades linearly with portfolio size, not exponentially with market volatility. This design principle prevents the failure mode where systems perform well in calm markets but degrade exactly when correctness matters most.

**Hermes lesson**: The paper trading engine must not throttle or batch operations during high-volatility periods. If hermes's paper engine uses a polling loop that processes events every N seconds, it must process them in a deterministic order regardless of market conditions. A paper engine that skips events during volatility is worse than no paper engine.

**Sources**:

- Optiver tech blog: <https://www.optiver.com/insights/technology-blog/engineering-the-three-pillars-of-trading-pricing-risk-and-execution/>

### 4.3 General HFT Engineering Principles (From Public Sources)

The following are well-documented principles from the HFT engineering literature that apply to any trading system, including swing strategies:

**The Deployment Invariant** (Knight Capital's lesson, universalized): Every server in a trading cluster must run identical code. Deployment verification must be automated and must fail loudly if any server is out of sync. In hermes's context: if paper trading and live trading share infrastructure components (WebSocket handlers, position managers), they must share the same code version. A paper-mode "shortcut" that differs from live code is a latent divergence bug.

**The Dead Code Rule**: Code that is disabled or deprecated but not deleted is a time bomb. Knight Capital's Power Peg was "disabled" for 8 years; it killed the firm when accidentally re-enabled. In hermes: any feature flag or A/B path that is not actively tested in paper mode is effectively dead code that will exhibit unexpected behavior if ever triggered live.

**The Position-Strategy Invariant**: Every open position must be traceable to an active strategy and a specific order ID. No "orphan positions." This is the invariant that Knight Capital violated — positions accumulated that belonged to no strategy.

**The Alert-to-Halt Pathway**: An alert that cannot automatically halt order flow is not a safety system; it is a logging system. Every safety alert in hermes must have a defined automatic response: either halt, reduce size, or escalate to human intervention. Alerts that merely log are worthless during a fast-moving failure.

---

## 5. Regulatory / Systemic Failures

### 5.1 FTX Collapse (November 2022) — Trading Bot Behavior During Exchange Death Spiral

**Timeline**: Nov 2–12, 2022. FTX went from seeming solvent to bankruptcy in 10 days.

**What happened to bots**: Trading bots running on FTX during the collapse faced three failure modes simultaneously:

1. **Orders accepted, never settled**: FTX's matching engine continued accepting orders through Nov 10. Bots that executed "profitable" trades during Nov 8–10 generated paper profits that could never be withdrawn because customer funds had already been misappropriated by Alameda Research.

2. **Withdrawal halts invisible to bots**: Bots have no mechanism to detect exchange insolvency. A bot's position P&L was positive; its account balance showed positive; but those numbers reflected an exchange ledger that had no backing assets. No API endpoint reported "your funds are gone."

3. **Post-bankruptcy limbo**: When FTX halted withdrawals and filed for bankruptcy (Nov 11), bots still connected to the API received valid responses. Some continued executing. The bots did not know the exchange was dead.

**The paper trading parallel**: Any paper trading system that runs against a live exchange (including Binance testnet) faces a version of this: if the testnet becomes unavailable or returns stale data, the paper bot continues executing against phantom prices. The paper system must implement exchange health checks and halt if the exchange is not confirming orders within an expected time window.

**FTX-specific statistics**: FTX's collapse contributed to $5B+ in losses for institutional and retail traders who held assets on the platform. Alameda Research had been funded by customer deposits since the Terra-LUNA collapse in May 2022, which destroyed Alameda's balance sheet.

**Hermes lesson**: Hermes must implement an exchange health monitor that:

- Confirms round-trip order acknowledgment within N ms
- Validates that fills reported via WebSocket match fills visible via REST (cross-validation)
- Implements automatic halt if no response received within timeout
- This applies equally to paper and live modes

**Sources**:

- FTX collapse timeline: <https://www.techtarget.com/whatis/feature/FTX-scam-explained-Everything-you-need-to-know>
- KPMG analysis: <https://assets.kpmg.com/content/dam/kpmg/sg/pdf/2022/12/the-collapse-of-ftx-1.pdf>
- Alameda balance confirmation: <https://www.fxstreet.com/cryptocurrencies/news/alameda-researchs-balance-history-on-ftx-confirms-death-blow-happened-during-luna-collapse-expert-says-202309120314>

### 5.2 Crypto Flash Crashes — Bot Behavior in Extreme Illiquidity

**The May 29, 2019 Kraken BTC/CAD Flash Crash**: BTC/CAD collapsed from C$11,800 to C$101 (~99%) in minutes on a single trading pair. Cause: low liquidity on a minor trading pair + a large market sell order swept all bids. Price recovered immediately. Any bot running a market sell order in that window would have received a fill at C$101 — a 99% loss on the position.

**The September 2019 Binance BTC/BUSD Flash Crash**: BTC/BUSD briefly collapsed to $1,800. Similar mechanism: thin order book on a new pair, large market order, cascading stop-losses.

**The October 2021 Binance BTC 87% crash**: Bitcoin briefly appeared to crash 87% on Binance in what Binance called an "apparent mistake." A Binance BTC pair dropped from ~$60,000 to ~$8,600 momentarily.

**Bot behavior in flash crashes**:

- Stop-loss orders (market sells) trigger at the stop price but fill at the market price — which during a flash crash can be 50–99% below the stop price. This is "stop hunting" amplified.
- Grid bots in a flash crash buy all the way down — correctly executing their strategy while the asset potentially doesn't recover.
- Paper trading systems with simulated fills at mid-price will report "filled at stop price" during the flash crash. Live bots get "filled at $101" when stop was at $11,000.

**Hermes lesson**: The swing strategy holds for multi-minutes to multi-days — it is unlikely to be active during a sub-second flash crash. However, the stop-loss mechanism must use exchange-native stop orders (not bot-managed stops) to ensure the fill happens at exchange level even if the bot disconnects. A bot-managed stop that requires the bot to be running when the price hits the stop level is a single point of failure.

**LME Nickel March 8, 2022 — Non-Crypto but Structurally Identical**:

LME nickel rose 270% in 3 trading days (from $27,080 to $101,365/metric ton). LME suspended trading and retroactively canceled $3.9 billion in transactions. This is the regulatory equivalent of a rollback — trades that were executed and confirmed were declared void.

**Hermes relevance**: This cannot happen on Binance spot for legal reasons (trades are final). But it illustrates the principle: extreme illiquidity events can force exchange-level intervention that is invisible to trading systems until after the fact. Paper trading systems that assume "confirmed = settled" have no model for retroactive cancellations.

**Sources**:

- Kraken flash crash: mentioned in London Post trading bot failures article (<https://london-post.co.uk/10-notorious-cases-of-trading-bot-failures/>)
- LME independent review: <https://www.lme.com/-/media/Files/Trading/New-initiatives/Nickel-independent-review/Independent-Review-of-Events-in-the-Nickel-Market-in-March-2022---Final-Report.pdf>
- LME fragility analysis: <https://eco3min.fr/en/lme-nickel-squeeze-2022-market-fragility/>
- OFR working paper on LME trade cancellation: <https://www.financialresearch.gov/working-papers/files/OFRwp-24-09_central-clearing-and-trade-cancellation.pdf>

### 5.3 Exchange Withdrawal Freezes — The "Paper Says Profit, Can't Withdraw" Problem

Multiple exchanges have frozen withdrawals during market stress:

- Celsius Network (June 2022): froze withdrawals citing "extreme market conditions"; funds never recovered for most users
- Voyager Digital (July 2022): froze withdrawals and filed for bankruptcy
- FTX (November 2022): froze withdrawals 72 hours before bankruptcy filing
- Various smaller CEXes during the 2022 bear market

**Bot behavior**: Bots continued trading on these platforms after withdrawal freezes. P&L showed positive or negative numbers. The numbers were meaningless — the exchange no longer honored them.

**Paper trading parallel**: A paper trading system running on Binance testnet has no exposure to this failure — testnet funds are not real. But a paper trading system used as a "live shadow" (paper P&L compared to live P&L for calibration) must be able to detect when its live counterpart's exchange has halted withdrawals and flag this as an unresolvable state.

**Hermes lesson**: Track the gap between "exchange-confirmed balance" and "withdrawable balance." If this gap grows (exchange shows funds but prevents withdrawal), the system must alert and cease live trading. This is not a paper-trading responsibility but a live-trading risk management requirement that paper trading cannot model.

---

## 6. Academic Case Studies

### 6.1 Yin et al. — Implementation Risk (2026) [Extended from SA-0]

SA-0 cited this paper. SA-4 adds the specific defect inventory from the full paper text:

**Paper**: "Implementation Risk in Portfolio Backtesting: A Previously Unquantified Source of Error" (Yin et al., arXiv 2603.20319, 2026)

**Engines tested**: bt, vectorbt, Backtrader, cvxportfolio + purpose-built reference. Three additional engines excluded during QC: Zipline-Reloaded (critical calendar defect), NautilusTrader (silent truncation after 62 days), QuantConnect LEAN (not specified).

**Seven specific defects found**:

| # | Engine | Defect | Category |
| --- | --- | --- | --- |
| 1 | Backtrader | `percabs` parameter divides user-specified commission by 100 before applying it — 100× cost undercharge | Cost-model bug |
| 2 | Backtrader | Fill-ordering logic rejects simultaneous buy/sell orders | Infrastructure bug |
| 3 | NautilusTrader | Silently truncates results after 62 trading days | Infrastructure bug |
| 4 | NautilusTrader | HEDGING mode creates separate long/short positions instead of netting | Specification divergence |
| 5 | NautilusTrader | Fixed-point arithmetic overflows at scale | Complexity sensitivity |
| 6 | Zipline-Reloaded | Calendar caching produces date misalignment | Architectural failure |
| 7 | cvxportfolio | [Not specified in extracted text] | — |

**Key quantitative results**:

- At zero transaction cost: all five engines produce identical equity curves → cost model is THE source of divergence
- Divergence scales monotonically with cost intensity: Spearman ρ=0.93, p<0.001
- Worst-case divergence (high-turnover strategy at 60 bps): 3.71% in total return
- Economic translation: each 0.10% annualized divergence = $1M annual ambiguity per $1B AUM
- Worst-case scenario: ~$37M/year ambiguity per $1B portfolio

**The Backtrader `percabs` bug is particularly instructive for hermes**: A parameter named "percentage absolute" silently divided by 100 before application. A user who set `commission=0.001` (meaning 0.1%) actually got `0.001 / 100 = 0.00001` (0.001%). Every backtest using this engine with this parameter was 100× too optimistic on transaction costs. The bug is trivially detectable with a unit test: "given a known trade, compute expected commission, assert computed commission matches."

**Hermes lesson**: Every component of the cost model (maker fee, taker fee, spread model, slippage model) must have a unit test that validates it against a known trade scenario at a known price with a known quantity. The test must fail if the component changes behavior. This is the only defense against the `percabs` class of silent bugs.

**Source**: <https://arxiv.org/html/2603.20319>

### 6.2 Claeys — Intrabar Ambiguity (2026) [Extended from SA-0]

SA-0 cited SSRN 6240638. SA-4 adds hermes-specific implications:

**The key number**: 18.47% of NQ futures bars contain both the stop-loss and take-profit price within the bar's range. In these cases, the backtesting engine must assume an order for which hit first. The gap between "SL always hits first" and "TP always hits first" assumptions reaches 3,695 NQ points per 1,000 trades.

**Why this matters for multi-day swing strategies**: The problem is worse for longer timeframes. A daily bar on BTC/USDT might have a range of $2,000–$3,000 (3–5% on a $60,000 BTC). A swing strategy with a 1.5% SL and 3% TP will frequently have both levels within the same daily bar. The backtest cannot know which was hit first without tick data.

**Hermes response**: The paper trading engine should use 1-minute OHLCV bars (or better: live tick data) to determine which of SL/TP was hit first within a broader timeframe bar. This is the "bar magnifier" approach. Using daily bars for a swing strategy and assuming SL always hits before TP (pessimistic) or TP always before SL (optimistic) introduces systematic bias in both directions.

**Source**: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6240638>

### 6.3 Bailey, López de Prado et al. — Probability of Backtest Overfitting (PBO)

**Papers**:

- "The Probability of Backtest Overfitting" (Bailey, Borwein, López de Prado, Zhu, 2015). Journal of Computational Finance 20(4):39-69. SSRN 2326253.
- "The Deflated Sharpe Ratio" (Bailey, López de Prado, 2014). Journal of Portfolio Management 40(5):94-107. SSRN 2460551.

**PBO Framework**: Combinatorially Symmetric Cross-Validation (CSCV) method that estimates the probability that a strategy selected based on backtest performance will underperform out-of-sample. If you run N backtests and pick the best, PBO estimates how likely that selected strategy is to fail on new data.

**The Deflated Sharpe Ratio (DSR)**: Adjusts the observed Sharpe ratio for the number of trials, sample length, and non-normality. A strategy with Sharpe 2.0 on 2 years of daily data tested in 100 parameter combinations has a DSR much lower than 2.0.

**Practical implication for hermes**: If the swing strategy undergoes hyperparameter optimization (any parameter tuning whatsoever — window length, threshold, stop size), the backtest Sharpe is inflated. The DSR is the correct metric to report. Any paper trading certification gate must compare live paper Sharpe against DSR, not raw backtest Sharpe.

**Sources**:

- PBO paper: <https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf>
- DSR paper: <https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf>
- SSRN PBO: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- SSRN DSR: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

### 6.4 Bouchaud, Farmer, Lillo — Market Impact (2009)

**Paper**: "How Markets Slowly Digest Changes in Supply and Demand" (Bouchaud, Farmer, Lillo, 2009). Handbook of Financial Markets: Dynamics and Evolution. SSRN 1266681.

**Key finding**: Revealed market liquidity is extremely low. Large orders can only be traded incrementally over periods as long as months. Order flow is a highly persistent long-memory process. Market impact of a trade is not instantaneous — it decays slowly over subsequent bars.

**Implication for paper trading**: A paper trading fill model that assumes zero market impact (buy 1 BTC at mid-price, no price movement) underestimates the true cost of large positions. For hermes's swing strategy, this matters when the trade size is large relative to average daily volume (ADV). Binance BTC/USDT ADV is ~$2–5B; a $10,000 trade has negligible impact. But as hermes scales or extends to smaller altcoins, impact modeling becomes necessary.

**Source**: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1266681>

### 6.5 Cont, Stoikov, Talreja — Stochastic Order Book Model (2010)

**Paper**: "A Stochastic Model for Order Book Dynamics" (Cont, Stoikov, Talreja, 2010). Operations Research 58(3):549-563.

**Key finding**: Order book dynamics can be modeled as a continuous-time stochastic process with tractable analytical properties. The model enables computation of: probability of an increase in midprice, probability of execution at bid before ask moves, and probability of a round-trip fill before price moves.

**Implication for hermes**: These probabilities are what the paper trading fill model should ideally use for limit order fill probability. NautilusTrader's FillModel (issue #2194, cited by SA-0) uses a simple scalar `prob_fill_on_limit` that ignores queue position, market depth, and volatility. The Cont et al. model offers a rigorous alternative calibratable from Binance order book data.

**For MVP1**, using a fixed `prob_fill_on_limit` is acceptable. The Cont et al. model is a V2 upgrade path.

**Source**: <https://pubsonline.informs.org/doi/10.1287/opre.1090.0780>

### 6.6 Cont, Cucuringu, Kochems, Prenzel — GAN-Based LOB Simulation (SSRN 4512356)

**Paper**: "Limit Order Book Simulation with Generative Adversarial Networks" (Cont, Cucuringu, Kochems, Prenzel, SSRN 2023).

**Key finding**: A GAN trained on real LOB data can generate realistic order book scenarios that reproduce empirical stylized facts and market impact properties. This is a generative model for hermes's fill simulator.

**Practical status**: This is research-grade; not yet productized for retail use. Cited here as the state-of-the-art reference for what "real" LOB simulation looks like.

**Source**: <https://ssrn.com/abstract=4512356>

### 6.7 FinRL-X — Simulation Fidelity Architecture (2026)

**Paper**: "FinRL-X: An AI-Native Modular Infrastructure for Quantitative Trading" (arXiv 2603.21330, 2026).

**Key architectural decision for reducing backtest-to-live divergence**: All strategy components output portfolio weight vectors through a unified interface, ensuring "consistent weight semantics across research, backtesting, broker simulation, and live execution." This is the weight-centric interface pattern.

**Paper trading validation**: FinRL-X ran paper trading (broker-integrated) from October 2025 to March 2026 against a prior backtest period. Results: +19.76% paper vs. SPY -2.51%, with order rejection rates "consistently low" and minimal weight tracking error.

**Structural divergences they identify**:

- Backtest → Paper: oversimplified fills, unrealistic transaction costs, survivorship bias
- Paper → Live: fill uncertainty (latency, partial fills, slippage), liquidity and queue position effects, API behavior differences, infrastructure fragility

**Source**: <https://arxiv.org/html/2603.21330v1>

---

## 7. Operational Failures Inventory

These are the unglamorous, infrastructure-level failures that production crypto bots encounter. They are not algorithmically interesting but they kill profitability and correctness.

### 7.1 WebSocket Reconnection — State Drift on Close-1006

**Mechanism**: WebSocket close code 1006 (abnormal closure, no close frame) is the most common disconnect signal after idle periods. It is the hardest to debug because there is no error message, no reason string, no close frame payload.

**State lost on disconnect**:

- Messages that arrived during the gap (fills, cancellations, order updates)
- Sequence number continuity (cannot quantify the gap)
- Private channel subscriptions (may need re-authentication)
- Authentication tokens (may expire during disconnect)

**Position drift pattern**:

1. Bot has open limit buy order
2. WebSocket drops (1006)
3. Exchange fills the limit order during the gap
4. Bot reconnects, re-fetches order status via REST — ONLY IF it implemented this reconciliation
5. If no REST reconciliation: bot thinks order is still open, continues managing a position it doesn't know it has

**Monitoring that catches it**:

- `last_message_ago_ms` — detects idle timeouts vs. sudden drops
- `missed_messages_count` — derived from sequence number gaps after reconnect
- `reconnect_success` rate and latency
- Post-reconnect REST reconciliation: compare local position state against exchange REST API; alert if discrepancy > threshold

**Hermes defense**: POST-RECONNECT RECONCILIATION IS MANDATORY. On every WebSocket reconnect, hermes must:

1. Fetch all open orders via REST
2. Fetch current positions via REST
3. Compare against local state
4. Apply diff and alert if discrepancy found
5. Only resume operation after reconciliation confirms local state matches exchange state

**Sources**:

- MatrixTrak WebSocket 1006 analysis: <https://matrixtrak.com/blog/websocket-close-code-1006-abnormal-closure-trading-bots>
- DEV Community (MatrixTrak): <https://dev.to/matrixtrak/websocket-closed-with-1006-why-trading-bots-lose-connection-without-an-error-code-26ld>
- NowNodes WebSocket best practices: <https://nownodes.io/blog/websocket-best-practices-for-production-reconnects-heartbeats-and-missed-events/>

### 7.2 Clock Drift — Timestamp Rejection

**Mechanism**: Exchanges use timestamp-signed requests for security. Binance's default `recvWindow` is 5,000ms; maximum allowed is 60,000ms. If the bot's clock is off by more than `recvWindow`, every private endpoint request (order placement, order cancellation, account query) returns a signature error.

**Failure signature**:

- Public endpoints (price, order book, klines) continue working normally
- Private endpoints return 401/403 with "timestamp for this request is outside of the recvWindow"
- The bot appears to be receiving market data but all orders silently fail
- If the bot does not distinguish between "order rejected by exchange" and "order placement failed due to network/auth," it may assume orders are live when they were never submitted

**Common triggers of clock drift**:

- VM pause/resume (cloud instance hibernation)
- Container migration
- NTP upstream loss
- Host under load (NTP synchronization fails when CPU is saturated)
- Docker time desynchronization on some hosts

**Hermes defense**:

- At startup: validate `abs(local_time - exchange_serverTime) < 1000ms`; halt if exceeding
- On every WebSocket reconnect: re-validate clock offset
- On every order placement failure: check if failure is auth-related (timestamp) vs. order-related (insufficient balance, etc.)
- Run NTP sync check as a system health monitor; alert if offset grows

**Binance-specific**: Binance serverTime endpoint: `GET /api/v3/time`. RTT-corrected offset: `offset_ms = server_ts - (t0 + (t1 - t0)/2)` where t0 and t1 bracket the request.

**Sources**:

- MatrixTrak timestamp drift: <https://matrixtrak.com/blog/timestamp-drift-signature-errors>
- QuantVPS NTP guide: <https://www.quantvps.com/blog/ntp-time-synchronization-in-trading>
- Coin Bureau bot mistakes: <https://coinbureau.com/guides/crypto-trading-bot-mistakes-to-avoid>

### 7.3 Rate Limit Misconfiguration — Burst Rejection Cascades

**Mechanism**: Binance uses a weight-based rate limit system. Each endpoint has a weight cost; the budget resets per minute. Exceeding the budget triggers HTTP 429 (back off); repeatedly violating after 429 triggers HTTP 418 (IP ban, duration: 2 minutes to 3 days, escalating).

**Critical Binance-specific trap**: Rate limits are **IP-based, not API-key-based**. Multiple bot instances on the same IP share a single weight budget. Three bots on the same server each with their own API key share the same 6,000-weight budget, split three ways.

**Failure mode**: A bot correctly implements per-key rate limiting but runs on a shared server with other bots. All bots behave correctly individually but collectively exhaust the shared IP budget. Orders start failing. If the bot treats rate-limit failures as "order rejected" instead of "infrastructure failure," it may enter a retry storm — each retry costs more weight, accelerating the ban.

**Paper trading gap**: If paper trading uses mocked REST calls (no real API requests), it will never surface this failure. Paper trading must make real API calls to market data endpoints so that rate limit pressure is realistic.

**Binance rate limit reference**:

- REST API: 6,000 weight/minute per IP; 1,200 weight/minute for order endpoints
- WebSocket API: 100 connections/IP; 300 messages/second/connection
- HTTP 429: back off; HTTP 418: IP banned (includes `Retry-After` header)

**Hermes defense**:

- Implement a weight tracker that counts every API call's weight cost in real time
- Configure burst budget headroom: stay below 80% of limit to absorb spikes
- On 429: implement exponential backoff immediately, no retry storms
- On 418: halt all trading, alert, wait for `Retry-After`
- Monitor weight budget as a time-series metric; alert when sustained usage exceeds 60% of budget

**Sources**:

- Binance rate limits: <https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/rate-limits>
- Binance ban documentation: <https://academy.binance.com/en/articles/how-to-avoid-getting-banned-by-rate-limits>
- Medium (rate limit guide): <https://medium.com/@saidur48/mastering-binance-api-rate-limits-how-to-track-api-weights-dynamically-in-python-44cb545d839d>

### 7.4 Silent Order Rejection — The Nondeterministic Response Problem

SA-0 documented this for Alpaca (HTTP 422, HTTP 200 + delayed reject, or silent drop). The pattern exists on Binance too.

**Binance-specific**: "Rejected or unsuccessful orders might or might not update the ORDERS rate limit type." This means: after a rejected order, the rate limit counter may or may not reflect the failed attempt. Bots that use the rate limit counter to determine if an order was processed cannot rely on it for rejected orders.

**Silent rejection patterns on Binance**:

- Order rejected immediately: REST returns HTTP 400 with error code
- Order rejected asynchronously: REST returns HTTP 200, order appears in order list with status REJECTED after a delay
- Order rejected with no WebSocket update: order status update only visible via REST polling, not pushed via WebSocket

**Hermes defense**:

- Every order placement must be followed by a confirmed acknowledgment with order ID
- Order IDs must be tracked; any order without a confirmed FILLED or CANCELED status after timeout must be explicitly queried via REST
- The reconciliation loop must run on every order, not just on reconnect
- Never infer order status from the absence of a WebSocket message

### 7.5 DST / Timezone Bugs

**The failure class**: Strategy logic that hardcodes "market hours" in local time (e.g., "only trade between 9:00–17:00 EST") will fire at the wrong times during DST transitions. Crypto markets are 24/7, so this is less critical for hermes than for equity strategies. However, any logic that references exchange-specific hours (e.g., Binance daily candle reset at 00:00 UTC) or data cutoffs must use UTC exclusively, never local time.

**Documented pattern**: MQL5 forum threads discuss "session breaks" that fail during DST changes. TradingView has a dedicated "DST-AWARE" session indicator because the default session indicator breaks at DST transitions.

**Hermes defense**:

- All timestamps stored and computed in UTC. No local time anywhere in core logic.
- DST awareness only at display layer (UI).
- Any scheduled job (e.g., daily P&L report, weekly rebalance check) must use UTC cron schedules.

**Sources**:

- TradersPost DST guide: <https://blog.traderspost.io/article/managing-daylight-saving-time-dst-changes-on-traderspost>
- EasyIndicators DST article: <https://easyindicators.substack.com/p/how-daylight-saving-time-affects>

### 7.6 Partial Fill Handling — The Incomplete Position Problem

**Failure pattern**: Bot places a limit buy for 1.0 BTC. Exchange partially fills 0.4 BTC and leaves 0.6 BTC as open order. Bot's position tracker increments by 1.0 BTC (assuming full fill). Bot now has an incorrect position size that affects all downstream risk calculations.

**Where this is documented**:

- IBKR paper trading: "first fill price okay, but subsequent prices sometimes way off" — partial fill handling bug in the paper trading engine itself
- NautilusTrader issue #2194: no partial fill simulation in the FillModel
- Freqtrade: assumes all limit orders fill fully; no partial fill handling in backtest

**Hermes defense**:

- Position tracking must update on each fill event, not on order placement
- Fill events carry filled quantity, not order quantity
- The paper trading engine must simulate partial fills: for limit orders, model `fill_fraction = min(1.0, available_depth_at_price / order_size)` with available depth sampled from the order book snapshot
- Cumulative fill tracking: sum of fills ≤ order quantity; alert if sum > order quantity (overfill bug)

---

## 8. Hermes Defense Matrix

| Failure Class | Root Cause | What Monitoring Catches It | Hermes Design Response | Implementation Priority |
| --- | --- | --- | --- | --- |
| WebSocket disconnect → position drift | State lost on 1006 disconnect; no reconnect reconciliation | `missed_messages_count` > 0 post-reconnect; position discrepancy alert | MANDATORY: full REST reconciliation on every reconnect before resuming trading | P0 — must ship with MVP1 |
| Clock drift → order rejection | NTP desync; VM pause/resume; container migration | `abs(local_ts - exchange_serverTime) > 1000ms` at startup or reconnect | Startup gate: validate clock offset; periodic background check every 60s; halt if drift exceeds 2000ms | P0 — must ship with MVP1 |
| Fill model illusion (always-fill) | Paper trading assumes unlimited liquidity | Fill rate divergence: paper fill rate > 95% while live fill rate < 80% | Probabilistic fill model: sample order book depth at price; model `fill_fraction`; log all unfilled paper orders | P1 — ship with MVP1 paper mode |
| Cost model silent bug (percabs-class) | Fee parameter silently transformed before application | Unit test: known trade → expected commission → assert match | Unit tests for fee model covering maker/taker rates, BNB discount, volume tiers, with known fixtures | P0 — must ship with MVP1 |
| Rate limit burst → IP ban | IP-shared budget exhausted; retry storm | Weight budget tracker; 429/418 HTTP status codes | Weight tracker with 80% budget ceiling; exponential backoff on 429; immediate halt + alert on 418 | P0 — must ship with MVP1 |
| Orphan position (Knight Capital class) | Position not attributable to any active strategy/order | `len(orphan_positions) > 0` | Position invariant: every position must map to an order ID and strategy; assertion check on every position update | P1 |
| Intrabar ambiguity (SL and TP same bar) | OHLCV bar cannot determine which was hit first | Disagreement between optimistic and pessimistic fill assumption | Use 1m bar or live tick to determine fill order within a broader timeframe bar | P1 — required for accurate paper P&L |
| Exchange death spiral | Exchange insolvent; API still accepts orders | Round-trip ACK timeout; fill confirmation gap > N seconds | Exchange health monitor: confirm WebSocket ping-pong; cross-validate fills REST vs WS; halt if health check fails | P2 — important for live, less critical for paper-only MVP1 |
| DST / timezone bug | Local time in strategy logic | Incorrect trade timing on DST transition days | All timestamps in UTC; no local time in core; UTC cron for scheduled jobs | P0 — must be enforced at code review |
| Silent order rejection | Exchange returns 200 but rejects async | No WebSocket fill event received for placed order after timeout | Every order must be explicitly confirmed via REST query within timeout; alert if status = REJECTED or absent | P1 |
| Partial fill miscount | Position tracker updates on order, not on fill event | Position size inconsistency: cumulative fills ≠ position size | Fill-event-driven position tracking; cumulative fill aggregation per order | P1 |
| Backtest overfitting (PBO) | Multiple parameter trials inflate out-of-sample Sharpe | Deflated Sharpe Ratio < raw Sharpe by significant margin | Report DSR alongside raw Sharpe for any optimized parameter set; enforce paper trading gate uses DSR | P2 — tooling before parameter optimization |
| Multi-timeframe candle alignment | Live open bar ≠ closed backtest bar for informative pairs | Dry-run vs. backtest signal timing divergence | Parameterize signal computation by "closed bar only" mode; test both modes; use `closed_only=True` by default | P1 |
| API key compromise (3Commas class) | API keys stored on third-party server | N/A — architectural prevention required | Testnet credentials only in paper mode; never transmit API keys to intermediaries; rotate on schedule | P0 — architecture requirement |
| Lookahead bias (TV Pine Script class) | Fill uses same bar's close that generated signal | Fill timestamp ≤ signal timestamp in test data | Enforce: signal at bar T → fill at bar T+1 open; test suite must assert this invariant | P0 — must ship with MVP1 |

---

## 9. Confidence Assessment

| Section | Confidence | Notes |
| --- | --- | --- |
| Knight Capital (§2) | HIGH | Multiple primary sources: SEC filing, technical post-mortems, settlement record |
| 3Commas breach (§3.1) | HIGH | Primary sources: Halborn analysis, official 3Commas disclosure, on-chain verification by ZachXBT |
| LUNA/UST grid bot wipeout (§3.2) | HIGH | Well-documented collapse; bot behavior documented by community and academic analysis (Harvard Law, MIT Sloan) |
| TradingView Pine Script failures (§3.3) | HIGH | Documented in TradingView's own support pages and Pine Script FAQ |
| Freqtrade dry-run divergence (§3.4) | HIGH | Primary source: GitHub issues with specific numbers and maintainer acknowledgment |
| Cryptohopper paper-vs-live (§3.5) | HIGH | Primary source: official Cryptohopper documentation |
| Retail slippage shock (§3.6) | MEDIUM | Specific $9M example from Paybis blog citing Changelly; not independently verified |
| Jane Street engineering lessons (§4.1) | HIGH | Primary source: official podcast episodes with verified titles and guest names |
| Optiver engineering lessons (§4.2) | HIGH | Primary source: official Optiver tech blog |
| FTX collapse (§5.1) | HIGH | Multiple independent sources; widely documented public record |
| Crypto flash crashes (§5.2) | MEDIUM | Kraken BTC/CAD flash crash well-documented; specific Binance 2019 crash details less clear; closest match is September 2019 BUSD crash |
| LME nickel (§5.2) | HIGH | Official LME independent review report; OFR working paper |
| Withdrawal freezes (§5.3) | HIGH | Public record; multiple exchanges documented |
| Yin et al. 2026 (§6.1) | HIGH | Full paper text reviewed; specific defects extracted |
| Bailey/López de Prado PBO (§6.3) | HIGH | Peer-reviewed papers; widely cited |
| Bouchaud/Farmer/Lillo (§6.4) | HIGH | Foundational published work |
| Cont et al. LOB model (§6.5, §6.6) | HIGH | Published in Operations Research; SSRN preprint for GAN paper |
| FinRL-X (§6.7) | MEDIUM | arXiv preprint; paper trading results over 5-month window — limited statistical significance |
| WebSocket 1006 (§7.1) | HIGH | Primary source: MatrixTrak technical blog with specific diagnostic detail |
| Clock drift (§7.2) | HIGH | Primary source: MatrixTrak timestamp drift analysis; Binance API docs |
| Rate limits (§7.3) | HIGH | Primary source: official Binance API documentation |
| DST bugs (§7.5) | MEDIUM | Well-known pattern; no specific crypto exchange failure case documented with financial impact |
| Hermes defense matrix (§8) | MEDIUM-HIGH | Derived from above; implementation priorities are author's assessment |

---

## 10. Key Citations

### Primary Sources — Regulatory

1. **SEC Release 34-70694** (Knight Capital, Oct 2013) — <https://www.headlandstech.jp/static/file/34-70694.pdf>
2. **LME Independent Review of Nickel Market Events** (Jan 2023) — <https://www.lme.com/-/media/Files/Trading/New-initiatives/Nickel-independent-review/Independent-Review-of-Events-in-the-Nickel-Market-in-March-2022---Final-Report.pdf>
3. **OFR Working Paper: Central Clearing and Trade Cancellation** (2024) — <https://www.financialresearch.gov/working-papers/files/OFRwp-24-09_central-clearing-and-trade-cancellation.pdf>

### Primary Sources — Technical Post-Mortems

1. **Speculative Branches: Knight Capital Disaster** — <https://specbranch.com/posts/knight-capital/>
2. **Henrico Dolfing: Knight Capital Case Study** — <https://www.henricodolfing.ch/en/case-study-4-the-440-million-software-error-at-knight-capital/>
3. **Postmortems.app: Knight Capital / SMARS** — <https://postmortems.app/postmortem/ef3cc3e0-c51c-4f5d-8dd0-cc8a8dfdcbb2>
4. **Halborn: 3Commas Breach Analysis** (Dec 2022) — <https://www.halborn.com/blog/post/explained-the-3commas-breach-december-2022>
5. **3Commas Official Breach Disclosure** — <https://3commas.io/blog/notice-on-api-data-disclosure-incident>

### Primary Sources — Exchange / Platform Docs

1. **Cryptohopper: Paper vs. Live Differences** — <https://support.cryptohopper.com/en/articles/9173649-are-there-differences-between-trading-with-paper-trading-and-real-funds>
2. **Binance Rate Limits Documentation** — <https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/rate-limits>
3. **Binance IP Ban Documentation** — <https://academy.binance.com/en/articles/how-to-avoid-getting-banned-by-rate-limits>

### Primary Sources — Engineering Blogs

 1. **MatrixTrak: WebSocket 1006 Failures in Trading Bots** — <https://matrixtrak.com/blog/websocket-close-code-1006-abnormal-closure-trading-bots>
 2. **MatrixTrak: Timestamp Drift and Signature Errors** — <https://matrixtrak.com/blog/timestamp-drift-signature-errors>
 3. **Optiver: Engineering the Three Pillars of Trading** — <https://www.optiver.com/insights/technology-blog/engineering-the-three-pillars-of-trading-pricing-risk-and-execution/>
 4. **Jane Street: Signals and Threads Podcast** — <https://signalsandthreads.com/>
 5. **Paybis: How to Backtest a Crypto Bot** — <https://paybis.com/blog/how-to-backtest-crypto-bot/>
 6. **QuantVPS: NTP Time Synchronization for Trading** — <https://www.quantvps.com/blog/ntp-time-synchronization-in-trading>

### Academic Papers

 1. **Yin et al. (2026): Implementation Risk in Portfolio Backtesting** — <https://arxiv.org/abs/2603.20319>
 2. **Claeys (2026): Intrabar Ambiguity** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6240638>
 3. **Bailey, Borwein, López de Prado, Zhu (2015): Probability of Backtest Overfitting** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253> | Full paper: <https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf>
 4. **Bailey, López de Prado (2014): Deflated Sharpe Ratio** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551> | Full paper: <https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf>
 5. **Bouchaud, Farmer, Lillo (2009): How Markets Slowly Digest Changes in Supply and Demand** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1266681>
 6. **Cont, Stoikov, Talreja (2010): A Stochastic Model for Order Book Dynamics** — <https://pubsonline.informs.org/doi/10.1287/opre.1090.0780> | PDF: <http://rama.cont.perso.math.cnrs.fr/pdf/CST2010.pdf>
 7. **Cont, Cucuringu, Kochems, Prenzel (2023): Limit Order Book Simulation with GANs** — <https://ssrn.com/abstract=4512356>
 8. **Yin et al. (2026): FinRL-X** — <https://arxiv.org/abs/2603.21330>
 9. **Arian, Norouzi, Seco (2024): Backtest Overfitting in the ML Era** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4686376>
10. **Wiecki et al. (2016): All That Glitters Is Not Gold (Quantopian)** — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220>

### Community / Forum Sources

 1. **Freqtrade #8518: Backtest / Live Performance Difference** — <https://github.com/freqtrade/freqtrade/issues/8518>
 2. **Freqtrade #7502: Backtest vs Dryrun Results** — <https://github.com/freqtrade/freqtrade/issues/7502>
 3. **Freqtrade #12894: Dry-run Signals Differ from Backtest** — <https://github.com/freqtrade/freqtrade/issues/12894>
 4. **Freqtrade #8451: Backtest vs Dry-run Difference** — <https://github.com/freqtrade/freqtrade/issues/8451>
 5. **Medium: "I Tested 23 Trading Bots and Lost $14,000"** — <https://medium.com/@nancycampbell896azhelenwlidxh/i-tested-23-trading-bots-and-lost-14-000-heres-what-actually-works-a4bc81f2c0af>
 6. **Coin Bureau: Crypto Trading Bot Mistakes** — <https://coinbureau.com/guides/crypto-trading-bot-mistakes-to-avoid>
 7. **Harvard Law: Anatomy of a Run — LUNA Crash** — <https://corpgov.law.harvard.edu/2023/05/22/anatomy-of-a-run-the-terra-luna-crash/>
 8. **TradingView Support: Unrealistic Results from Lookahead** — <https://es.tradingview.com/support/solutions/43000614705/>

---

*SA-4 post-mortem survey complete. Cross-reference with SA-0 landscape (`research/paper-trading-sa0-landscape`) for baseline framework failures. Feed into SA-5 calibration harness for certification gate design.*
