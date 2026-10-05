"""A synthetic market that reaches hermes through the production wire format.

Prices are invented so a test can force any path through the engine, but every
response has the shape, the endpoint and the timing of what production
receives: Binance spot klines, server time and depth, and Frankfurter daily
rates. The real adapters read it through an ``httpx`` mock transport, so the
request URLs, query parameters, JSON parsing, retries and closed-bar rule are
the production code paths (issue #688).

- :mod:`.script` builds the price path.
- :mod:`.wire` renders it as the vendors' JSON.
- :mod:`.schema` checks a payload against the vendor contract; the recorded
  production fixtures and every generated payload pass the same checks.
- :mod:`.exchange` answers the adapters' requests on a simulated clock.
- :mod:`.harness` wires ``hermes serve`` to it.
"""
