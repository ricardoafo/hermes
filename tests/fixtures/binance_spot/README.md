# Binance spot account replies

`account.json` is shaped like the reply Binance's spot REST API gives to the
signed `GET /api/v3/account` (field names, string amounts, the `free` /
`locked` split), with made-up amounts and uid. A read-only key reports
`canTrade: false`. `tests/unit/adapters/exchanges/test_binance_balance_reader.py`
serves it through respx, so no test reaches Binance.

`open_orders.json` is shaped like the reply to the signed
`GET /api/v3/openOrders?symbol=BTCUSDT`, with made-up ids and prices: two
stop-loss-limit sells (one partly filled), a limit buy and a stop-loss-limit
buy. `tests/unit/adapters/exchanges/test_binance_spot_stops.py` serves it
through respx to check that only the stop-loss sells are read as resting
stops (#1080).
