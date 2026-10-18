- **`.env.local.example` additions** for planned + newly-added config surface:
  - `HERMES_SHADOW` — placeholder for the shadow-mode flag arriving in #175.
    Explains three-gate rule (`SHADOW=false` + `ENV=prod` + `ALLOW_MAINNET=yes`)
    for real-money enablement in MVP2.
  - Binance testnet ↔ mainnet split — replaces the generic `HERMES_BINANCE__*`
    with explicit `HERMES_BINANCE_TESTNET__*` (dev/sandbox) and
    `HERMES_BINANCE_MAINNET__*` (prod, with shadow gate). Documents the
    "Enable Reading only" permission rule for MVP1 mainnet keys.
  - Kraken section — documents the two-scenario branching (Futures Demo defer
    vs mainnet with shadow gate) pending operator's URL confirmation.
  - `HERMES_LEDGER__DB_PATH` — path override for the SQLite ledger from #134.

Pure documentation — no code path reads these values yet (shadow flag lands
with #175). Prevents future drift where operator sets a var but the example
still shows the old shape.
