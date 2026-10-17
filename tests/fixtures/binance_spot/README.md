# Binance spot account replies

`account.json` is shaped like the reply Binance's spot REST API gives to the
signed `GET /api/v3/account` (field names, string amounts, the `free` /
`locked` split), with made-up amounts and uid. A read-only key reports
`canTrade: false`. `tests/unit/adapters/exchanges/test_binance_balance_reader.py`
serves it through respx, so no test reaches Binance.
