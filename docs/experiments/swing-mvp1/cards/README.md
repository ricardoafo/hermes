# Hypothesis cards

One card per research candidate (ADR-021 §1 stage 1, ADR-012 D-11, ADR-026 §1). A card names the mechanism, the
expected sign, a cap on free parameters and a falsification test. It is committed, and its SHA-256 goes into the
batch document, before any run. A card is never edited once a batch that cites it is signed; a change is a new card.

Every card in this folder shares the experiment's trial log (`../trial-log.jsonl`), so every run of every card counts
toward the DSR of all of them.

Common to every card unless it says otherwise: pre-registration v2's universe (BTC, ETH, SOL on Binance spot),
capital (5,000 USDT), windows, fees, slippage, close-based exit pricing (§6.2), sizing (1% risk to the stop, 5% cap,
15% exposure, 3 positions, correlation lock at 0.80) and the ADR-021 §5 funnel floors.

Free parameters: at most 3 per card. Each has a base value and two neighbours, all fixed here. One Train touch logs
the base, one neighbour run per neighbour value (one parameter moved at a time) and the base at 2× costs, so at most
8 trials, as v2 did.
