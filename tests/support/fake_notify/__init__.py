"""Recording fakes for every message and ping Hermes sends (issue #972).

In a lower environment, Telegram, Slack, healthchecks and Sentry all point at
one loopback service that records each call in the vendor's wire format, so a
scenario can assert on exactly what Rick would have seen, drive the read-only
Telegram commands (ADR-015) like a user, and inject outages.

- :mod:`.schema` checks a payload against the vendor contract; the fakes, the
  recorded fixtures and the daily contract run use the same checks.
- :mod:`.server` is the service, with a Python API and a control API.
"""
