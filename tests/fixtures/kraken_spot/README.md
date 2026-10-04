# Kraken spot adapter replies

Replies shaped like Kraken's spot REST documentation, with made-up ids and
amounts: the private `AddOrder`, `QueryOrders`, `CancelOrder` and
`BalanceEx`, plus the public `Depth` and `AssetPairs` that the adapter reads
through the public adapter. `tests/unit/adapters/exchanges/test_kraken_spot.py`
serves them through respx, so no test reaches Kraken. Kraken has no spot
testnet, so these are not recorded from a live account.
