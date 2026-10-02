"""Portfolio application layer — the tracker the bar loop drives.

Public surface:

- :class:`~hermes.application.portfolio.tracker.PortfolioTracker` — the current snapshot
  behind ``mark``, ``apply_fill`` and ``state``

Imports domain only (enforced by the ``application-imports-only-domain-and-ports``
contract).

Reference: 308-signal-event-portfolio-state change.md D-4.
"""

from hermes.application.portfolio.tracker import PortfolioTracker

__all__ = ["PortfolioTracker"]
